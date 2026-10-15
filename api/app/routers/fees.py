"""Fee plans, overrides, tax rates, invoices and payments (BRD FR-FE-01 to FR-FE-05, FR-FE-09).

Amounts are rupees with two decimals on the wire ("15000.00") and whole paise in the database.
A client's fees are shown to its owner and to Administrators only (FR-FE-05). Staff ask for a
client-specific fee; an Administrator approves or rejects it. Tax rates are added by an
Administrator from a date that has not started yet, so no invoice already raised could have used
a different rate than the one it records.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.access import RecordAccess, authorize_record
from app.auth.deps import Principal, get_db, require_roles
from app.catalog import paise, rupees
from app.errors import ApiHTTPException
from app.fees.controls import decide_adjustment, policy, request_adjustment
from app.fees.ledger import (
    STATUS_LABEL,
    FeeStatus,
    LedgerRefused,
    fees_summary,
    invoice_status,
    lock_invoice,
    paid_by_invoice,
    record_payment,
    reverse,
)
from app.fees.plans import OverrideRefused, decide, pending_for, plan_of, request_override
from app.fees.tax import GST
from app.models import (
    FIRST_CLIENT_ID,
    LAST_CLIENT_ID,
    AdjustmentKind,
    AdjustmentStatus,
    BillingFrequency,
    Client,
    EntryKind,
    FeeAdjustment,
    FeeOverride,
    Invoice,
    LedgerEntry,
    OverduePolicyMode,
    OverrideStatus,
    PaymentMode,
    Role,
    TaxRate,
    User,
)
from app.routers.clients import load_client
from app.timeutil import today_ist, utcnow

router = APIRouter(tags=["fees"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
admin_only = require_roles(Role.ADMINISTRATOR)

ClientIdPath = Annotated[int, Path(ge=FIRST_CLIENT_ID, le=LAST_CLIENT_ID)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]
Money = Annotated[Decimal, Field(gt=0, le=Decimal("99999999.99"), decimal_places=2)]


class OverrideOut(BaseModel):
    id: int
    client_id: int
    client_name: str
    original: str
    amount: str
    reason: str
    status: OverrideStatus
    requested_by: str | None
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None


class AdjustmentOut(BaseModel):
    id: int
    client_id: int
    client_name: str
    invoice_id: int
    invoice_no: str
    kind: AdjustmentKind
    kind_label: str
    amount: str
    reason: str
    status: AdjustmentStatus
    requested_by: str | None
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    credit_note_no: str | None


ADJUSTMENT_LABEL = {
    AdjustmentKind.CREDIT_NOTE: "Credit note",
    AdjustmentKind.DISCOUNT: "Discount",
    AdjustmentKind.WRITE_OFF: "Write-off",
}


class PlanOut(BaseModel):
    package_name: str
    package_version: int
    frequency: BillingFrequency
    package_fee: str
    fee: str  # what invoices use now
    override: OverrideOut | None  # the approved override in force, if any


class InvoiceOut(BaseModel):
    id: int
    status: FeeStatus
    status_label: str
    invoice_no: str
    period_label: str
    issue_date: date
    due_date: date
    description: str
    amount: str
    tax_rate: str  # percent, e.g. "18.00"
    cgst: str
    sgst: str
    igst: str
    tax: str  # CGST + SGST + IGST
    total: str
    paid: str
    balance: str


class LedgerOut(BaseModel):
    id: int
    invoice_id: int
    invoice_no: str
    kind: EntryKind
    amount: str
    entry_date: date
    mode: PaymentMode | None
    reference: str | None
    note: str | None
    reverses_id: int | None
    reversed: bool  # a payment that a later reversal cancels
    by: str | None
    at: datetime


class FeesOut(BaseModel):
    plan: PlanOut
    pending: OverrideOut | None
    overrides: list[OverrideOut]  # every request, newest first
    invoices: list[InvoiceOut]  # newest first
    ledger: list[LedgerOut]  # payments and reversals, newest first; nothing is ever removed
    adjustments: list[AdjustmentOut]  # credit notes, discounts, write-offs; pending change nothing
    status: FeeStatus | None  # None: no invoices yet
    status_label: str | None
    balance: str  # still owed across all invoices
    can_set_fee: bool  # an Administrator sets it at once; Staff ask


class OverrideIn(BaseModel):
    amount: Money
    reason: Reason


class DecisionIn(BaseModel):
    approve: bool
    note: Note | None = None


class PaymentIn(BaseModel):
    amount: Money
    paid_on: date
    mode: PaymentMode
    reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = (
        None
    )
    note: Note | None = None


class ReverseIn(BaseModel):
    reason: Reason


class AdjustmentIn(BaseModel):
    kind: AdjustmentKind
    amount: Money
    reason: Reason


class PolicyIn(BaseModel):
    mode: OverduePolicyMode
    limit: Annotated[Decimal, Field(ge=0, le=Decimal("99999999.99"), decimal_places=2)]


class PolicyOut(BaseModel):
    mode: OverduePolicyMode
    limit: str
    updated_by: str | None
    updated_at: datetime


class TaxRateIn(BaseModel):
    rate: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)]  # percent
    effective_from: date
    note: Note | None = None


class TaxRateOut(BaseModel):
    id: int
    code: str
    rate: str
    effective_from: date
    note: str | None
    added_by: str | None
    added_at: datetime
    in_force: bool


def _name(db: Session, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return user.full_name if user else None


def _override_out(db: Session, o: FeeOverride, client: Client | None = None) -> OverrideOut:
    client = client or db.get(Client, o.client_pk)
    if client is None:  # a foreign key prevents it
        raise RuntimeError("override without its client")
    return OverrideOut(
        id=o.id,
        client_id=client.client_id,
        client_name=client.legal_name,
        original=rupees(o.original_paise) or "0.00",
        amount=rupees(o.amount_paise) or "0.00",
        reason=o.reason,
        status=o.status,
        requested_by=_name(db, o.requested_by_id),
        requested_at=o.requested_at,
        decided_by=_name(db, o.decided_by_id),
        decided_at=o.decided_at,
        decision_note=o.decision_note,
    )


def adjustment_out(
    db: Session, a: FeeAdjustment, client: Client | None = None, invoice_no: str | None = None
) -> AdjustmentOut:
    client = client or db.get(Client, a.client_pk)
    if invoice_no is None:
        invoice_no = db.scalar(select(Invoice.invoice_no).where(Invoice.id == a.invoice_id))
    if client is None or invoice_no is None:  # foreign keys prevent it
        raise RuntimeError("adjustment refers to missing rows")
    return AdjustmentOut(
        id=a.id,
        client_id=client.client_id,
        client_name=client.legal_name,
        invoice_id=a.invoice_id,
        invoice_no=invoice_no,
        kind=a.kind,
        kind_label=ADJUSTMENT_LABEL[a.kind],
        amount=rupees(a.amount_paise) or "0.00",
        reason=a.reason,
        status=a.status,
        requested_by=_name(db, a.requested_by_id),
        requested_at=a.requested_at,
        decided_by=_name(db, a.decided_by_id),
        decided_at=a.decided_at,
        decision_note=a.decision_note,
        credit_note_no=a.credit_note_no,
    )


def _percent(bp: int) -> str:
    return f"{bp // 100}.{bp % 100:02d}"


def invoice_out(i: Invoice, paid: int, today: date) -> InvoiceOut:
    status = invoice_status(i.total_paise, paid, i.due_date, today)
    return InvoiceOut(
        id=i.id,
        status=status,
        status_label=STATUS_LABEL[status],
        paid=rupees(paid) or "0.00",
        balance=rupees(max(i.total_paise - paid, 0)) or "0.00",
        invoice_no=i.invoice_no,
        period_label=i.period_label,
        issue_date=i.issue_date,
        due_date=i.due_date,
        description=i.description,
        amount=rupees(i.amount_paise) or "0.00",
        tax_rate=_percent(i.tax_rate_bp),
        cgst=rupees(i.cgst_paise) or "0.00",
        sgst=rupees(i.sgst_paise) or "0.00",
        igst=rupees(i.igst_paise) or "0.00",
        tax=rupees(i.cgst_paise + i.sgst_paise + i.igst_paise) or "0.00",
        total=rupees(i.total_paise) or "0.00",
    )


def _fees_out(db: Session, principal: Principal, client: Client) -> FeesOut:
    plan = plan_of(db, client)
    pending = pending_for(db, client)
    overrides = db.scalars(
        select(FeeOverride)
        .where(FeeOverride.client_pk == client.id)
        .order_by(FeeOverride.id.desc())
        .limit(50)
    )
    invoices = list(
        db.scalars(
            select(Invoice)
            .where(Invoice.client_pk == client.id)
            .order_by(Invoice.issue_date.desc(), Invoice.id.desc())
            .limit(200)
        )
    )
    today = today_ist()
    paid = paid_by_invoice(db, (i.id for i in invoices))
    entries = db.execute(
        select(LedgerEntry, User.full_name, Invoice.invoice_no)
        .join(Invoice, Invoice.id == LedgerEntry.invoice_id)
        .outerjoin(User, User.id == LedgerEntry.created_by_id)
        .where(LedgerEntry.client_pk == client.id)
        .order_by(LedgerEntry.id.desc())
        .limit(500)
    ).all()
    reversed_ids = {e.reverses_id for e, _, _ in entries if e.reverses_id is not None}
    adjustments = db.execute(
        select(FeeAdjustment, Invoice.invoice_no)
        .join(Invoice, Invoice.id == FeeAdjustment.invoice_id)
        .where(FeeAdjustment.client_pk == client.id)
        .order_by(FeeAdjustment.id.desc())
        .limit(200)
    ).all()
    summary = fees_summary(db, [client.id], today)[client.id]
    return FeesOut(
        plan=PlanOut(
            package_name=plan.package.name,
            package_version=plan.version.version,
            frequency=plan.frequency,
            package_fee=rupees(plan.package_fee) or "0.00",
            fee=rupees(plan.fee) or "0.00",
            override=_override_out(db, plan.override, client) if plan.override else None,
        ),
        pending=_override_out(db, pending, client) if pending else None,
        overrides=[_override_out(db, o, client) for o in overrides],
        invoices=[invoice_out(i, paid[i.id], today) for i in invoices],
        ledger=[
            LedgerOut(
                id=e.id,
                invoice_id=e.invoice_id,
                invoice_no=number,
                kind=e.kind,
                amount=rupees(e.amount_paise) or "0.00",
                entry_date=e.entry_date,
                mode=e.mode,
                reference=e.reference,
                note=e.note,
                reverses_id=e.reverses_id,
                reversed=e.id in reversed_ids,
                by=name,
                at=e.created_at,
            )
            for e, name, number in entries
        ],
        adjustments=[adjustment_out(db, a, client, no) for a, no in adjustments],
        status=summary.status,
        status_label=STATUS_LABEL[summary.status] if summary.status else None,
        balance=rupees(summary.balance) or "0.00",
        can_set_fee=principal.role is Role.ADMINISTRATOR,
    )


@router.get("/api/clients/{client_id}/fees")
def client_fees(
    client_id: ClientIdPath,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FeesOut:
    """The client's fee plan, fee changes and invoices: for its owner and Administrators."""
    client = load_client(db, principal, client_id)
    return _fees_out(db, principal, client)


@router.post("/api/clients/{client_id}/fee-overrides", status_code=201)
def ask_for_fee(
    client_id: ClientIdPath,
    body: OverrideIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FeesOut:
    """FR-FE-01: a client-specific fee. Staff ask; an Administrator's takes effect at once."""
    client = load_client(db, principal, client_id, lock=True)
    try:
        override = request_override(
            db,
            client=client,
            amount_paise=paise(body.amount),
            reason=body.reason,
            user_id=principal.user_id,
            role=principal.role,
        )
    except OverrideRefused as refused:
        db.rollback()
        raise ApiHTTPException(409, str(refused), code=refused.code) from None
    audit(
        db,
        "fee.override_approved"
        if override.status is OverrideStatus.APPROVED
        else "fee.override_requested",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("client", client.client_id),
        details={
            "override": override.id,
            "original": override.original_paise,
            "amount": override.amount_paise,
        },
    )
    db.commit()
    return _fees_out(db, principal, client)


def _invoice_for(db: Session, principal: Principal, invoice_id: int) -> tuple[Invoice, Client]:
    """The invoice, locked, if this person may see its client's fees (FR-FE-05)."""
    invoice = lock_invoice(db, invoice_id)
    client = db.get(Client, invoice.client_pk) if invoice else None
    if invoice is None or client is None:
        authorize_record(principal, None, what="invoice")  # raises 404
        raise ApiHTTPException(404, "Not found.", code="not_found")
    authorize_record(principal, RecordAccess(owner_id=client.owner_id), what="invoice")
    return invoice, client


@router.post("/api/invoices/{invoice_id}/payments", status_code=201)
def pay(
    invoice_id: Annotated[int, Path(ge=1)],
    body: PaymentIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FeesOut:
    """FR-FE-03: record a payment or part payment against an invoice."""
    invoice, client = _invoice_for(db, principal, invoice_id)
    try:
        entry = record_payment(
            db,
            invoice,
            amount_paise=paise(body.amount),
            paid_on=body.paid_on,
            mode=body.mode,
            reference=body.reference or None,
            note=body.note or None,
            user_id=principal.user_id,
        )
    except LedgerRefused as refused:
        db.rollback()
        raise ApiHTTPException(409, str(refused), code=refused.code) from None
    audit(
        db,
        "payment.recorded",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("invoice", invoice.invoice_no),
        details={"entry": entry.id, "amount": entry.amount_paise, "mode": body.mode.value},
    )
    db.commit()
    return _fees_out(db, principal, client)


@router.post("/api/ledger/{entry_id}/reverse", status_code=201)
def reverse_entry(
    entry_id: Annotated[int, Path(ge=1)],
    body: ReverseIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FeesOut:
    """FR-FE-03: cancel a payment with a reversing entry; both stay in the ledger."""
    invoice_id = db.scalar(select(LedgerEntry.invoice_id).where(LedgerEntry.id == entry_id))
    if invoice_id is None:
        authorize_record(principal, None, what="ledger entry")  # raises 404
        raise ApiHTTPException(404, "Not found.", code="not_found")
    invoice, client = _invoice_for(db, principal, invoice_id)
    entry = db.get(LedgerEntry, entry_id)
    if entry is None:  # entries are never deleted
        raise RuntimeError("ledger entry vanished")
    try:
        reversal = reverse(db, entry, reason=body.reason, user_id=principal.user_id)
    except LedgerRefused as refused:
        db.rollback()
        raise ApiHTTPException(409, str(refused), code=refused.code) from None
    audit(
        db,
        "payment.reversed",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("invoice", invoice.invoice_no),
        details={"entry": reversal.id, "reverses": entry.id, "amount": entry.amount_paise},
    )
    db.commit()
    return _fees_out(db, principal, client)


@router.post("/api/invoices/{invoice_id}/adjustments", status_code=201)
def ask_for_adjustment(
    invoice_id: Annotated[int, Path(ge=1)],
    body: AdjustmentIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FeesOut:
    """FR-FE-08: a credit note, discount or write-off. Staff ask; an Administrator approves."""
    invoice, client = _invoice_for(db, principal, invoice_id)
    try:
        adjustment = request_adjustment(
            db,
            invoice,
            kind=body.kind,
            amount_paise=paise(body.amount),
            reason=body.reason,
            user_id=principal.user_id,
            role=principal.role,
        )
    except LedgerRefused as refused:
        db.rollback()
        raise ApiHTTPException(409, str(refused), code=refused.code) from None
    audit(
        db,
        "fee.adjustment_approved"
        if adjustment.status is AdjustmentStatus.APPROVED
        else "fee.adjustment_requested",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("invoice", invoice.invoice_no),
        details={
            "adjustment": adjustment.id,
            "kind": adjustment.kind.value,
            "amount": adjustment.amount_paise,
            "credit_note": adjustment.credit_note_no,
        },
    )
    db.commit()
    return _fees_out(db, principal, client)


@router.get("/api/admin/fee-adjustments")
def pending_adjustments(
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[AdjustmentOut]:
    """Credit notes, discounts and write-offs waiting for an Administrator, oldest first."""
    rows = db.scalars(
        select(FeeAdjustment)
        .where(FeeAdjustment.status == AdjustmentStatus.PENDING)
        .order_by(FeeAdjustment.requested_at, FeeAdjustment.id)
        .limit(500)
    )
    return [adjustment_out(db, a) for a in rows]


@router.post("/api/admin/fee-adjustments/{adjustment_id}/decision")
def decide_on_adjustment(
    adjustment_id: Annotated[int, Path(ge=1)],
    body: DecisionIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AdjustmentOut:
    invoice_id = db.scalar(
        select(FeeAdjustment.invoice_id).where(FeeAdjustment.id == adjustment_id)
    )
    invoice = lock_invoice(db, invoice_id) if invoice_id is not None else None
    adjustment = db.scalar(
        select(FeeAdjustment)
        .where(FeeAdjustment.id == adjustment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if invoice is None or adjustment is None:
        raise ApiHTTPException(404, "Not found.", code="not_found")
    try:
        decide_adjustment(
            db,
            adjustment,
            invoice,
            approve=body.approve,
            note=body.note,
            user_id=principal.user_id,
        )
    except LedgerRefused as refused:
        db.rollback()
        raise ApiHTTPException(409, str(refused), code=refused.code) from None
    audit(
        db,
        "fee.adjustment_approved" if body.approve else "fee.adjustment_rejected",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("invoice", invoice.invoice_no),
        details={
            "adjustment": adjustment.id,
            "kind": adjustment.kind.value,
            "amount": adjustment.amount_paise,
            "credit_note": adjustment.credit_note_no,
        },
    )
    db.commit()
    return adjustment_out(db, adjustment, invoice_no=invoice.invoice_no)


def _policy_out(db: Session) -> PolicyOut:
    row = policy(db)
    return PolicyOut(
        mode=row.mode,
        limit=rupees(row.limit_paise) or "0.00",
        updated_by=_name(db, row.updated_by_id),
        updated_at=row.updated_at,
    )


@router.get("/api/admin/fee-policy")
def get_fee_policy(
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PolicyOut:
    return _policy_out(db)


@router.put("/api/admin/fee-policy")
def set_fee_policy(
    body: PolicyIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PolicyOut:
    """FR-FE-06: reminders only, also warn on filings, or also block Filed over the limit."""
    row = policy(db, lock=True)
    before = {"mode": row.mode.value, "limit": row.limit_paise}
    row.mode = body.mode
    row.limit_paise = paise(body.limit)
    row.updated_by_id = principal.user_id
    row.updated_at = utcnow()
    audit(
        db,
        "fee.policy_changed",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("fee_policy", 1),
        details={"before": before, "after": {"mode": body.mode.value, "limit": row.limit_paise}},
    )
    db.commit()
    return _policy_out(db)


@router.get("/api/admin/fee-overrides")
def pending_fees(
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[OverrideOut]:
    """Fee changes waiting for an Administrator, oldest first."""
    rows = db.scalars(
        select(FeeOverride)
        .where(FeeOverride.status == OverrideStatus.PENDING)
        .order_by(FeeOverride.requested_at, FeeOverride.id)
        .limit(500)
    )
    return [_override_out(db, o) for o in rows]


@router.post("/api/admin/fee-overrides/{override_id}/decision")
def decide_fee(
    override_id: Annotated[int, Path(ge=1)],
    body: DecisionIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> OverrideOut:
    # Lock the client first, as asking does, so the two cannot interleave.
    client_pk = db.scalar(select(FeeOverride.client_pk).where(FeeOverride.id == override_id))
    if client_pk is not None:
        db.scalar(
            select(Client.id)
            .where(Client.id == client_pk)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    try:
        override = decide(
            db, override_id, approve=body.approve, note=body.note, user_id=principal.user_id
        )
    except OverrideRefused as refused:
        db.rollback()
        status = 404 if refused.code == "not_found" else 409
        raise ApiHTTPException(status, str(refused), code=refused.code) from None
    client = db.get(Client, override.client_pk)
    audit(
        db,
        "fee.override_approved" if body.approve else "fee.override_rejected",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("client", client.client_id if client else override.client_pk),
        details={
            "override": override.id,
            "original": override.original_paise,
            "amount": override.amount_paise,
        },
    )
    db.commit()
    return _override_out(db, override, client)


def _rates(db: Session) -> list[TaxRateOut]:
    today = today_ist()
    rows = list(
        db.scalars(
            select(TaxRate)
            .where(TaxRate.code == GST)
            .order_by(TaxRate.effective_from.desc())
            .limit(100)
        )
    )
    current = next((r.id for r in rows if r.effective_from <= today), None)
    return [
        TaxRateOut(
            id=r.id,
            code=r.code,
            rate=_percent(r.rate_bp),
            effective_from=r.effective_from,
            note=r.note,
            added_by=_name(db, r.created_by_id),
            added_at=r.created_at,
            in_force=r.id == current,
        )
        for r in rows
    ]


@router.get("/api/admin/tax-rates")
def tax_rates(
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[TaxRateOut]:
    return _rates(db)


@router.post("/api/admin/tax-rates", status_code=201)
def add_tax_rate(
    body: TaxRateIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[TaxRateOut]:
    """FR-FE-09: a new GST rate from a date that has not started yet."""
    if body.effective_from <= today_ist():
        raise ApiHTTPException(
            422,
            "A new rate must start on a later date, so invoices already raised keep theirs.",
            code="date_in_past",
        )
    bp = int((body.rate * 100).to_integral_value())
    if db.scalar(
        select(TaxRate.id).where(TaxRate.code == GST, TaxRate.effective_from == body.effective_from)
    ):
        raise ApiHTTPException(409, "A rate already starts on that date.", code="duplicate")
    rate = TaxRate(
        code=GST,
        rate_bp=bp,
        effective_from=body.effective_from,
        note=body.note or None,
        created_by_id=principal.user_id,
    )
    db.add(rate)
    db.flush()
    audit(
        db,
        "tax_rate.added",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("tax_rate", rate.id),
        details={"code": GST, "rate_bp": bp, "from": body.effective_from.isoformat()},
    )
    db.commit()
    return _rates(db)
