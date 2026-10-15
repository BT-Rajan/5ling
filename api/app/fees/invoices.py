"""Raising invoices with gapless numbers (BRD FR-FE-02, FR-FE-09).

The series row for the prefix and financial year is created if missing (INSERT IGNORE in its own
short transaction, so two runs at once cannot both create it), then locked with SELECT ... FOR UPDATE, and its counter
moved on by one in the same transaction that writes the invoice. Another transaction wanting a
number waits for the lock; if this one rolls back, the counter rolls back with it and the number
is used by the next invoice instead. A database trigger refuses any invoice that does not take
the number its counter has just issued.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.fees.periods import Period, fy_label
from app.fees.plans import Plan
from app.fees.tax import is_inter_state, rate_on, tax_for
from app.models import BillingFrequency, Client, Invoice, InvoiceSeries

FREQUENCY_LABEL = {
    BillingFrequency.MONTHLY: "Monthly",
    BillingFrequency.QUARTERLY: "Quarterly",
    BillingFrequency.YEARLY: "Yearly",
    BillingFrequency.ONE_TIME: "One-time",
}


class NoTaxRate(Exception):
    pass


def _ensure_series(db: Session, prefix: str, fy: str) -> None:
    """Create the series row, if missing, in its own short transaction.

    Inside the invoice transaction, INSERT IGNORE would take a shared lock on an existing row
    that the SELECT ... FOR UPDATE then upgrades; two invoices at once would deadlock.
    """
    bind = db.get_bind()
    engine = bind if isinstance(bind, Engine) else bind.engine
    with engine.connect() as conn:
        conn.execute(
            insert(InvoiceSeries)
            .values(prefix=prefix, financial_year=fy, next_seq=1)
            .prefix_with("IGNORE")
        )
        conn.commit()


def next_number(db: Session, prefix: str, issue_date: date) -> tuple[InvoiceSeries, int, str]:
    fy = fy_label(issue_date)
    existing = db.scalar(
        select(InvoiceSeries.id).where(
            InvoiceSeries.prefix == prefix, InvoiceSeries.financial_year == fy
        )
    )
    if existing is None:
        _ensure_series(db, prefix, fy)
    series = db.scalar(
        select(InvoiceSeries)
        .where(InvoiceSeries.prefix == prefix, InvoiceSeries.financial_year == fy)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if series is None:  # just inserted or already there
        raise RuntimeError("invoice series missing after insert")
    seq = series.next_seq
    series.next_seq = seq + 1
    db.flush()  # the counter moves before the invoice is written (the trigger checks it)
    return series, seq, f"{prefix}/{fy}/{seq:05d}"


def raise_invoice(
    db: Session,
    settings: Settings,
    *,
    client: Client,
    plan: Plan,
    period: Period,
    issue_date: date,
    created_by_id: int | None,
) -> Invoice:
    rate = rate_on(db, issue_date)
    if rate is None:
        raise NoTaxRate(f"no tax rate in force on {issue_date.isoformat()}")
    amount = plan.fee
    tax = tax_for(
        amount, rate.rate_bp, inter_state=is_inter_state(settings.firm_gst_state, client.gstin)
    )
    series, seq, number = next_number(db, settings.invoice_prefix, issue_date)
    invoice = Invoice(
        invoice_no=number,
        series_id=series.id,
        seq=seq,
        client_pk=client.id,
        period_key=period.key,
        period_label=period.label,
        period_start=period.start,
        period_end=period.end,
        issue_date=issue_date,
        due_date=issue_date + timedelta(days=settings.invoice_terms_days),
        description=f"{plan.package.name}: {FREQUENCY_LABEL[plan.frequency].lower()} fee, {period.label}"[
            :300
        ],
        package_version_id=plan.version.id,
        fee_override_id=plan.override.id if plan.override else None,
        tax_rate_id=rate.id,
        tax_rate_bp=rate.rate_bp,
        amount_paise=amount,
        cgst_paise=tax.cgst,
        sgst_paise=tax.sgst,
        igst_paise=tax.igst,
        total_paise=amount + tax.total,
        created_by_id=created_by_id,
    )
    db.add(invoice)
    db.flush()
    return invoice
