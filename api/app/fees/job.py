"""The invoice scheduler (BRD FR-FE-02). Run daily by deploy/systemd/ledgerline-invoices.timer.

For each active client, every billing period from the one the client joined in up to today's
that has no invoice yet gets one, dated today, at the client's current fee (the package fee or
an approved override). A one-time fee is invoiced once. Paused and closed clients are skipped.

Safe to run twice or at once: one run at a time holds a MySQL named lock, each client is handled
in its own transaction under its row lock, and a unique key allows one invoice per client per
period. If the job does not run for a while, the next run catches up on the missed periods.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.config import Settings
from app.fees.invoices import raise_invoice
from app.fees.periods import periods_due
from app.fees.plans import plan_of
from app.models import Client, ClientStatus, Invoice
from app.timeutil import IST, ensure_utc, utcnow

log = logging.getLogger("ledgerline.jobs")

_LOCK = "ledgerline.invoices"


@dataclass
class Tally:
    checked: int = 0
    raised: int = 0
    failed: int = 0
    skipped_reason: str | None = None
    failures: list[str] = field(default_factory=list)


def invoice_client(db: Session, settings: Settings, client_pk: int, today: date) -> list[Invoice]:
    """Raise this client's missing invoices up to today. The caller commits."""
    client = db.scalar(
        select(Client)
        .where(Client.id == client_pk)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if client is None or client.status is not ClientStatus.ACTIVE:
        return []
    plan = plan_of(db, client)
    joined = ensure_utc(client.created_at).astimezone(IST).date()
    done = set(db.scalars(select(Invoice.period_key).where(Invoice.client_pk == client.id)))
    made: list[Invoice] = []
    for period in periods_due(plan.frequency, joined, today):
        if period.key in done:
            continue
        invoice = raise_invoice(
            db,
            settings,
            client=client,
            plan=plan,
            period=period,
            issue_date=today,
            created_by_id=None,
        )
        audit(
            db,
            "invoice.raised",
            target=("invoice", invoice.invoice_no),
            details={
                "client_id": client.client_id,
                "period": period.key,
                "amount": invoice.amount_paise,
                "total": invoice.total_paise,
            },
        )
        made.append(invoice)
    return made


def run(
    factory: sessionmaker[Session], settings: Settings, *, now: datetime | None = None
) -> Tally:
    today = ensure_utc(now or utcnow()).astimezone(IST).date()
    tally = Tally()
    with factory() as lock_db:
        if lock_db.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": _LOCK}) != 1:
            tally.skipped_reason = "another run is in progress"
            return tally
        try:
            with factory() as db:
                ids = list(
                    db.scalars(
                        select(Client.id)
                        .where(Client.status == ClientStatus.ACTIVE)
                        .order_by(Client.id)
                    )
                )
            for client_pk in ids:
                tally.checked += 1
                with factory() as db:
                    try:
                        made = invoice_client(db, settings, client_pk, today)
                        db.commit()
                        tally.raised += len(made)
                    except Exception as exc:
                        db.rollback()
                        tally.failed += 1
                        tally.failures.append(f"client {client_pk}: {type(exc).__name__}")
                        log.exception("invoice failed", extra={"ctx": {"client": client_pk}})
        finally:
            lock_db.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK})
    facts = {"checked": tally.checked, "raised": tally.raised, "failed": tally.failed}
    if tally.failed:
        log.critical("INVOICE RUN FAILED", extra={"ctx": facts})
    else:
        log.info("invoices ran", extra={"ctx": facts})
    return tally
