"""Payments, reversals and fee status (BRD FR-FE-03, FR-FE-04).

An invoice's balance is its total, less payments, plus reversals of payments; it is worked out
from the ledger each time it is shown, so a status changes the moment a payment is recorded.
Payments against one invoice are serialised by a row lock on the invoice, so two people
recording at once cannot together pay more than the balance.

Status of an invoice: Paid (nothing left), Overdue (something left after its due date),
Part paid (something paid, something left, not yet due), Due (nothing paid, not yet due).
A client's status is the most pressing of its invoices': Overdue, then Part paid, then Due,
else Paid; a client with no invoices has none.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models import EntryKind, Invoice, LedgerEntry, PaymentMode
from app.timeutil import today_ist


class FeeStatus(enum.StrEnum):
    PAID = "paid"
    DUE = "due"
    PART_PAID = "part_paid"
    OVERDUE = "overdue"


STATUS_LABEL = {
    FeeStatus.PAID: "Paid",
    FeeStatus.DUE: "Due",
    FeeStatus.PART_PAID: "Part paid",
    FeeStatus.OVERDUE: "Overdue",
}
_URGENCY = (FeeStatus.OVERDUE, FeeStatus.PART_PAID, FeeStatus.DUE, FeeStatus.PAID)


class LedgerRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _signed() -> object:
    return func.coalesce(
        func.sum(
            case(
                (LedgerEntry.kind == EntryKind.PAYMENT, LedgerEntry.amount_paise),
                else_=-LedgerEntry.amount_paise,
            )
        ),
        0,
    )


def paid_by_invoice(db: Session, invoice_ids: Iterable[int]) -> dict[int, int]:
    ids = list(invoice_ids)
    if not ids:
        return {}
    rows = db.execute(
        select(LedgerEntry.invoice_id, _signed())  # type: ignore[call-overload]
        .where(LedgerEntry.invoice_id.in_(ids))
        .group_by(LedgerEntry.invoice_id)
    ).all()
    paid = {int(i): int(p) for i, p in rows}
    return {i: paid.get(i, 0) for i in ids}


def invoice_status(total: int, paid: int, due_date: date, today: date) -> FeeStatus:
    if paid >= total:
        return FeeStatus.PAID
    if due_date < today:
        return FeeStatus.OVERDUE
    return FeeStatus.PART_PAID if paid > 0 else FeeStatus.DUE


def most_pressing(statuses: Iterable[FeeStatus]) -> FeeStatus | None:
    found = set(statuses)
    return next((s for s in _URGENCY if s in found), None)


@dataclass(frozen=True)
class ClientFees:
    status: FeeStatus | None
    balance: int  # paise still owed across all invoices


def fees_summary(
    db: Session, client_pks: Iterable[int], today: date | None = None
) -> dict[int, ClientFees]:
    """Fee status and balance for many clients in two queries (for lists)."""
    pks = list(client_pks)
    if not pks:
        return {}
    today = today or today_ist()
    invoices = db.execute(
        select(Invoice.id, Invoice.client_pk, Invoice.total_paise, Invoice.due_date).where(
            Invoice.client_pk.in_(pks)
        )
    ).all()
    paid = paid_by_invoice(db, (i.id for i in invoices))
    statuses: dict[int, list[FeeStatus]] = {pk: [] for pk in pks}
    owed: dict[int, int] = dict.fromkeys(pks, 0)
    for inv in invoices:
        statuses[inv.client_pk].append(
            invoice_status(inv.total_paise, paid[inv.id], inv.due_date, today)
        )
        owed[inv.client_pk] += max(inv.total_paise - paid[inv.id], 0)
    return {pk: ClientFees(most_pressing(statuses[pk]), owed[pk]) for pk in pks}


def lock_invoice(db: Session, invoice_id: int) -> Invoice | None:
    # A row lock only: invoices are never updated (trigger), but locking them is allowed.
    return db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def record_payment(
    db: Session,
    invoice: Invoice,
    *,
    amount_paise: int,
    paid_on: date,
    mode: PaymentMode,
    reference: str | None,
    note: str | None,
    user_id: int,
) -> LedgerEntry:
    """A payment, or part payment, against a locked invoice."""
    if paid_on > today_ist():
        raise LedgerRefused("future_date", "A payment cannot be dated in the future.")
    balance = invoice.total_paise - paid_by_invoice(db, [invoice.id])[invoice.id]
    if balance <= 0:
        raise LedgerRefused("already_paid", "This invoice is already paid in full.")
    if amount_paise > balance:
        raise LedgerRefused(
            "overpayment",
            "That is more than is owed on this invoice. Record the balance, or check the amount.",
        )
    entry = LedgerEntry(
        client_pk=invoice.client_pk,
        invoice_id=invoice.id,
        kind=EntryKind.PAYMENT,
        amount_paise=amount_paise,
        entry_date=paid_on,
        mode=mode,
        reference=reference,
        note=note,
        created_by_id=user_id,
    )
    db.add(entry)
    db.flush()
    return entry


def reverse(db: Session, entry: LedgerEntry, *, reason: str, user_id: int) -> LedgerEntry:
    """Cancel a payment with a reversing entry. The caller locked its invoice."""
    if entry.kind is not EntryKind.PAYMENT:
        raise LedgerRefused("not_a_payment", "Only a payment can be reversed.")
    if db.scalar(select(LedgerEntry.id).where(LedgerEntry.reverses_id == entry.id)):
        raise LedgerRefused("already_reversed", "This payment has already been reversed.")
    reversal = LedgerEntry(
        client_pk=entry.client_pk,
        invoice_id=entry.invoice_id,
        kind=EntryKind.REVERSAL,
        amount_paise=entry.amount_paise,
        entry_date=today_ist(),
        note=reason,
        reverses_id=entry.id,
        created_by_id=user_id,
    )
    db.add(reversal)
    db.flush()
    return reversal
