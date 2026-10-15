"""Fee plans, tax rates and invoices (BRD FR-FE-01, FR-FE-02, FR-FE-09).

A client's fee is its package version's fee, unless an Administrator approved a client-specific
amount (a fee override) for that package version. Overrides keep the original amount, who asked,
and who approved or rejected them; one may be pending per client at a time (unique computed
column); they are never deleted, and a decided one never changes (triggers, migration 0022).

Invoices are numbered per series (prefix and financial year) from a counter row that is locked
and incremented in the same transaction that writes the invoice, so a number is used exactly
once: a rolled-back invoice gives its number back, a committed one keeps it. The database also
refuses an invoice whose number is not the one the counter just issued, and invoices and series
are never deleted; invoices are never changed (corrections arrive as credit notes, C26).
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
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


class OverrideStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FeeOverride(Base):
    __tablename__ = "fee_overrides"
    __table_args__ = (
        CheckConstraint("amount_paise > 0", name="amount_positive"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) AND (decided_at IS NULL) = (decided_by_id IS NULL)",
            name="decided_with_whom",
        ),
        Index("ix_fee_overrides_client_pk_id", "client_pk", "id"),
        Index("ix_fee_overrides_status", "status"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    # The package version it applies to: a change of package ends it.
    package_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("package_versions.id"), nullable=False
    )
    original_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[OverrideStatus] = mapped_column(
        _enum(OverrideStatus, "override_status"), nullable=False
    )
    requested_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    decided_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The client while the request is pending, else NULL: unique, so one pending per client.
    pending_client_pk: Mapped[int | None] = mapped_column(
        BigInteger,
        Computed("(if(`status` = 'pending',`client_pk`,NULL))", persisted=True),
        nullable=True,
        unique=True,
    )


class TaxRate(Base):
    """FR-FE-09: a tax rate from a date. New rates are added, never edited."""

    __tablename__ = "tax_rates"
    __table_args__ = (
        UniqueConstraint("code", "effective_from"),
        CheckConstraint("rate_bp >= 0 AND rate_bp <= 10000", name="rate_range"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), nullable=False)  # "GST"
    rate_bp: Mapped[int] = mapped_column(Integer, nullable=False)  # basis points: 1800 = 18%
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class InvoiceSeries(Base):
    """One counter per prefix and financial year; next_seq only ever goes up by one."""

    __tablename__ = "invoice_series"
    __table_args__ = (
        UniqueConstraint("prefix", "financial_year"),
        CheckConstraint("next_seq >= 1", name="next_seq_positive"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    prefix: Mapped[str] = mapped_column(String(4), nullable=False)
    financial_year: Mapped[str] = mapped_column(String(5), nullable=False)  # "26-27"
    next_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("series_id", "seq"),
        UniqueConstraint("client_pk", "period_key"),
        CheckConstraint("seq >= 1", name="seq_positive"),
        CheckConstraint(
            "amount_paise > 0 AND cgst_paise >= 0 AND sgst_paise >= 0 AND igst_paise >= 0",
            name="amounts_valid",
        ),
        CheckConstraint(
            "total_paise = amount_paise + cgst_paise + sgst_paise + igst_paise",
            name="total_adds_up",
        ),
        CheckConstraint("igst_paise = 0 OR (cgst_paise = 0 AND sgst_paise = 0)", name="one_kind"),
        Index("ix_invoices_client_pk_issue_date", "client_pk", "issue_date"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    invoice_no: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)
    series_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("invoice_series.id"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    period_key: Mapped[str] = mapped_column(String(16), nullable=False)
    period_label: Mapped[str] = mapped_column(String(40), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    package_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("package_versions.id"), nullable=False
    )
    fee_override_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("fee_overrides.id"), nullable=True
    )
    tax_rate_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tax_rates.id"), nullable=False)
    tax_rate_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cgst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sgst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    igst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )  # None: the scheduler
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
