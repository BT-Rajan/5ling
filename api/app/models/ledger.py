"""The fee ledger (BRD FR-FE-03).

Every money movement against an invoice is a row here, and rows are never changed or deleted
(triggers, migration 0023). A payment records amount, date, mode and reference; part payments
are simply payments for less than the balance. A mistake is corrected by a reversal: a new row
that points at the payment it cancels, for the same amount. Both stay visible, and the balance
counts the reversal back. A payment can be reversed once (unique key). Credit notes, discounts
and write-offs arrive in C26.
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, values_callable=lambda e: [m.value for m in e], name=name)


class EntryKind(enum.StrEnum):
    PAYMENT = "payment"
    REVERSAL = "reversal"


class PaymentMode(enum.StrEnum):
    BANK_TRANSFER = "bank_transfer"
    UPI = "upi"
    CHEQUE = "cheque"
    CASH = "cash"
    CARD = "card"
    OTHER = "other"


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint("(kind = 'reversal') = (reverses_id IS NOT NULL)", name="reversal_points"),
        CheckConstraint("(kind = 'payment') = (mode IS NOT NULL)", name="payment_has_mode"),
        Index("ix_ledger_entries_client_pk_id", "client_pk", "id"),
        Index("ix_ledger_entries_invoice_id_id", "invoice_id", "id"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id"), nullable=False)
    kind: Mapped[EntryKind] = mapped_column(_enum(EntryKind, "entry_kind"), nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_date: Mapped[date] = mapped_column(Date, nullable=False)  # the day paid, or reversed
    mode: Mapped[PaymentMode | None] = mapped_column(
        _enum(PaymentMode, "payment_mode"), nullable=True
    )
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reverses_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("ledger_entries.id"), nullable=True, unique=True
    )
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
