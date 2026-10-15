"""Fee adjustments and the overdue policy (BRD FR-FE-06, FR-FE-08).

Credit notes, discounts and write-offs are asked for against an invoice and change nothing
until an Administrator approves them; only then does a ledger entry reduce the balance (and the
database refuses an adjustment entry that has no approved adjustment behind it). An
Administrator's own adjustment is approved by them as they enter it. Approval re-checks the
balance under the invoice's lock, so approvals cannot together take it below zero. A credit
note is numbered from its own gapless series (CN/26-27/00001) as it is approved.

The overdue policy is set by an Administrator: reminders only; or also a warning on the
client's filings; or also refusing Filed for a client over the limit (more than `limit` overdue),
unless an Administrator has waived it, with a reason, for that one cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fees.invoices import next_number
from app.fees.ledger import LedgerRefused, paid_by_invoice
from app.models import (
    AdjustmentKind,
    AdjustmentStatus,
    Client,
    Cycle,
    EntryKind,
    FeeAdjustment,
    FeePolicy,
    FilingWaiver,
    Invoice,
    LedgerEntry,
    OverduePolicyMode,
    Role,
)
from app.timeutil import today_ist, utcnow

CREDIT_NOTE_PREFIX = "CN"


def _balance(db: Session, invoice: Invoice) -> int:
    return invoice.total_paise - paid_by_invoice(db, [invoice.id])[invoice.id]


def _apply(db: Session, adjustment: FeeAdjustment, invoice: Invoice, user_id: int) -> None:
    """Approve: number a credit note, then put the adjustment in the ledger."""
    if adjustment.amount_paise > _balance(db, invoice):
        raise LedgerRefused("exceeds_balance", "That is more than is now owed on this invoice.")
    number = None
    if adjustment.kind is AdjustmentKind.CREDIT_NOTE:
        # Taken first: the decision is one UPDATE (a decided adjustment never changes again).
        _, _, number = next_number(db, CREDIT_NOTE_PREFIX, today_ist())
    adjustment.status = AdjustmentStatus.APPROVED
    adjustment.decided_by_id = user_id
    adjustment.decided_at = utcnow()
    adjustment.credit_note_no = number
    db.flush()
    db.add(
        LedgerEntry(
            client_pk=adjustment.client_pk,
            invoice_id=invoice.id,
            kind=EntryKind(adjustment.kind.value),
            amount_paise=adjustment.amount_paise,
            entry_date=today_ist(),
            note=adjustment.reason,
            adjustment_id=adjustment.id,
            created_by_id=user_id,
        )
    )
    db.flush()


def request_adjustment(
    db: Session,
    invoice: Invoice,
    *,
    kind: AdjustmentKind,
    amount_paise: int,
    reason: str,
    user_id: int,
    role: Role,
) -> FeeAdjustment:
    """Ask for (Staff) or make (Administrator) an adjustment. The caller locked the invoice."""
    if amount_paise > _balance(db, invoice):
        raise LedgerRefused("exceeds_balance", "That is more than is owed on this invoice.")
    adjustment = FeeAdjustment(
        client_pk=invoice.client_pk,
        invoice_id=invoice.id,
        kind=kind,
        amount_paise=amount_paise,
        reason=reason,
        status=AdjustmentStatus.PENDING,
        requested_by_id=user_id,
    )
    db.add(adjustment)
    db.flush()
    if role is Role.ADMINISTRATOR:
        _apply(db, adjustment, invoice, user_id)
    return adjustment


def decide_adjustment(
    db: Session,
    adjustment: FeeAdjustment,
    invoice: Invoice,
    *,
    approve: bool,
    note: str | None,
    user_id: int,
) -> FeeAdjustment:
    """An Administrator decides a pending adjustment. The caller locked the invoice, then it."""
    if adjustment.status is not AdjustmentStatus.PENDING:
        raise LedgerRefused("already_decided", "This has already been decided.")
    adjustment.decision_note = (note or "").strip() or None
    if approve:
        _apply(db, adjustment, invoice, user_id)
        return adjustment
    if adjustment.decision_note is None:
        raise LedgerRefused("note_required", "Say why it is rejected.")
    adjustment.status = AdjustmentStatus.REJECTED
    adjustment.decided_by_id = user_id
    adjustment.decided_at = utcnow()
    db.flush()
    return adjustment


# --- The overdue policy ---------------------------------------------------------------------


def policy(db: Session, *, lock: bool = False) -> FeePolicy:
    stmt = select(FeePolicy).where(FeePolicy.id == 1)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:  # loaded by migration 0024, never deleted (trigger)
        raise RuntimeError("fee policy row missing")
    return row


def overdue_paise(db: Session, client_pk: int, today: date) -> int:
    invoices = db.execute(
        select(Invoice.id, Invoice.total_paise).where(
            Invoice.client_pk == client_pk, Invoice.due_date < today
        )
    ).all()
    paid = paid_by_invoice(db, (i.id for i in invoices))
    return sum(max(i.total_paise - paid[i.id], 0) for i in invoices)


@dataclass(frozen=True)
class FilingCheck:
    mode: OverduePolicyMode
    overdue: int  # paise overdue now
    over_limit: bool
    waiver: FilingWaiver | None

    @property
    def warn(self) -> bool:
        return self.over_limit and self.mode is not OverduePolicyMode.REMINDER

    @property
    def blocked(self) -> bool:
        return self.over_limit and self.mode is OverduePolicyMode.BLOCK and self.waiver is None


def filing_check(
    db: Session, client: Client, cycle: Cycle, today: date | None = None
) -> FilingCheck:
    rule = policy(db)
    overdue = overdue_paise(db, client.id, today or today_ist())
    waiver = db.scalar(select(FilingWaiver).where(FilingWaiver.cycle_id == cycle.id))
    return FilingCheck(rule.mode, overdue, overdue > rule.limit_paise, waiver)


def grant_waiver(db: Session, cycle: Cycle, *, reason: str, user_id: int) -> FilingWaiver:
    if db.scalar(select(FilingWaiver.id).where(FilingWaiver.cycle_id == cycle.id)):
        raise LedgerRefused("already_waived", "This filing already has an override.")
    waiver = FilingWaiver(cycle_id=cycle.id, reason=reason, granted_by_id=user_id)
    db.add(waiver)
    db.flush()
    return waiver
