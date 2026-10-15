"""The fee ledger (BRD FR-FE-03).

Every money movement against an invoice is a row here, and rows are never changed or deleted
(triggers, migration 0023). A payment records amount, date, mode and reference; part payments
are simply payments for less than the balance. A mistake is corrected by a reversal: a new row
that points at the payment it cancels, for the same amount. Both stay visible, and the balance
counts the reversal back. A payment can be reversed once (unique key).

Credit notes, discounts and write-offs (C26, FR-FE-08) are asked for as fee adjustments and
reach the ledger only when an Administrator approves them; until then they change nothing. The
overdue policy (FR-FE-06) is one row an Administrator sets; a filing waiver lets one cycle be
filed once despite the block.
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
    # Approved adjustments (C26, FR-FE-08): each reduces the balance like a payment.
    CREDIT_NOTE = "credit_note"
    DISCOUNT = "discount"
    WRITE_OFF = "write_off"


ADJUSTMENT_KINDS = (EntryKind.CREDIT_NOTE, EntryKind.DISCOUNT, EntryKind.WRITE_OFF)


class AdjustmentKind(enum.StrEnum):
    CREDIT_NOTE = "credit_note"
    DISCOUNT = "discount"
    WRITE_OFF = "write_off"


class OverduePolicyMode(enum.StrEnum):
    REMINDER = "reminder"  # reminders only (C35)
    WARN = "warn"  # and a warning on the client's filings
    BLOCK = "block"  # and Filed is refused unless an Administrator waives it once


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
        CheckConstraint(
            "(kind IN ('credit_note', 'discount', 'write_off')) = (adjustment_id IS NOT NULL)",
            name="adjustment_points",
        ),
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
    adjustment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fee_adjustments.id"), nullable=True, unique=True
    )
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class AdjustmentStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FeeAdjustment(Base):
    """A credit note, discount or write-off waiting for, or decided by, an Administrator."""

    __tablename__ = "fee_adjustments"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) AND (decided_at IS NULL) = (decided_by_id IS NULL)",
            name="decided_with_whom",
        ),
        CheckConstraint(
            "credit_note_no IS NULL OR (kind = 'credit_note' AND status = 'approved')",
            name="numbered_when_approved",
        ),
        Index("ix_fee_adjustments_status", "status"),
        Index("ix_fee_adjustments_client_pk_id", "client_pk", "id"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    invoice_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("invoices.id"), nullable=False)
    kind: Mapped[AdjustmentKind] = mapped_column(
        _enum(AdjustmentKind, "adjustment_kind"), nullable=False
    )
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[AdjustmentStatus] = mapped_column(
        _enum(AdjustmentStatus, "adjustment_status"), nullable=False
    )
    requested_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    decided_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Credit notes get a gapless number from their own series when approved (CN/26-27/00001).
    credit_note_no: Mapped[str | None] = mapped_column(String(16), nullable=True, unique=True)


class FeePolicy(Base):
    """FR-FE-06: one row (id 1), set by an Administrator."""

    __tablename__ = "fee_policy"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("limit_paise >= 0", name="limit_not_negative"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    mode: Mapped[OverduePolicyMode] = mapped_column(
        _enum(OverduePolicyMode, "policy_mode"), nullable=False
    )
    # A client is "over the limit" when more than this is overdue (0: anything overdue).
    limit_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class FilingWaiver(Base):
    """FR-FE-06: a one-time override, by an Administrator with a reason, for one cycle."""

    __tablename__ = "filing_waivers"
    __table_args__ = (_TABLE,)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cycles.id"), nullable=False, unique=True
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
