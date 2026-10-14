"""What the firm offers (BRD 7.2): filing types with their calendars, and packages of filings.

Calendars and packages are versioned (FR-PK-05). A version starts as a draft that the
Administrator may edit; publishing makes it final. Triggers (migration 0006) refuse any change to
a published version or to the filings and checklist items under it, so a cycle built from a
version can always point back at exactly what it was built from. A change is a new version.

Foreign keys here never cascade: a cascade would change rows without firing those triggers.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, values_callable=lambda e: [m.value for m in e], name=name)


class Frequency(enum.StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class PeriodBasis(enum.StrEnum):
    """Financial-year handling (FR-PK-02): which year the periods are counted in."""

    FINANCIAL_YEAR = "financial_year"  # April to March: Q1 is April to June
    CALENDAR_YEAR = "calendar_year"  # January to December


class HolidayRule(enum.StrEnum):
    """FR-PK-04: what to do when a due or alert date falls on a holiday or weekend."""

    EARLIER_WORKING_DAY = "earlier_working_day"
    AS_IS = "as_is"


class BillingFrequency(enum.StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"
    ONE_TIME = "one_time"


class Filing(Base):
    """A filing type, such as GSTR-1. Its schedule lives in its calendar versions."""

    __tablename__ = "filings"
    __table_args__ = _TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The checklist template: copied into a package when this filing is added, then edited there.
    default_checklist: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class CalendarVersion(Base):
    """One version of a filing type's schedule (FR-PK-02, FR-PK-03, FR-PK-04).

    The due date of a period is `due_day` of the month `due_months_after` months after the
    period's last month (a day past the month's end means its last day). `due_overrides` replaces
    that for named periods, e.g. {"Q4": {"months_after": 2, "day": 31}}. The dates themselves
    are worked out by the due-date engine (chunk C06).
    """

    __tablename__ = "calendar_versions"
    __table_args__ = (
        UniqueConstraint("filing_id", "version"),
        CheckConstraint("due_months_after BETWEEN 0 AND 12", name="months_after_range"),
        CheckConstraint("due_day BETWEEN 1 AND 31", name="day_range"),
        CheckConstraint("lead_days IS NULL OR lead_days BETWEEN 0 AND 90", name="lead_range"),
        CheckConstraint(
            "(published_at IS NULL) = (published_by_id IS NULL)", name="published_by_whom"
        ),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency: Mapped[Frequency] = mapped_column(_enum(Frequency, "frequency"), nullable=False)
    period_basis: Mapped[PeriodBasis] = mapped_column(
        _enum(PeriodBasis, "period_basis"), nullable=False
    )
    due_months_after: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    due_day: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    due_overrides: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # None means the default for the frequency (FR-PK-03; set in chunk C06).
    lead_days: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    holiday_rule: Mapped[HolidayRule] = mapped_column(
        _enum(HolidayRule, "holiday_rule"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )


class Package(Base):
    """A named offering. What it contains, and its fee, live in its versions."""

    __tablename__ = "packages"
    __table_args__ = _TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class PackageVersion(Base):
    """FR-PK-01: the filings included, the fee, billing frequency and checklists."""

    __tablename__ = "package_versions"
    __table_args__ = (
        UniqueConstraint("package_id", "version"),
        CheckConstraint("fee_paise IS NULL OR fee_paise > 0", name="fee_positive"),
        # FR-PK-01: never published without a fee (the filing rule is checked by the API).
        CheckConstraint(
            "published_at IS NULL OR (fee_paise IS NOT NULL AND billing_frequency IS NOT NULL)",
            name="published_has_fee",
        ),
        CheckConstraint(
            "(published_at IS NULL) = (published_by_id IS NULL)", name="published_by_whom"
        ),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("packages.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_paise: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 100 paise = ₹1
    billing_frequency: Mapped[BillingFrequency | None] = mapped_column(
        _enum(BillingFrequency, "billing_frequency"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )


class PackageFiling(Base):
    __tablename__ = "package_filings"
    __table_args__ = (UniqueConstraint("package_version_id", "filing_id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("package_versions.id"), nullable=False
    )
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id"), nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)


class ChecklistItem(Base):
    """One document a client is asked for, for one filing in one package version."""

    __tablename__ = "package_checklist_items"
    __table_args__ = _TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    package_filing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("package_filings.id"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)


class Holiday(Base):
    """A day the firm treats as non-working, besides Saturdays and Sundays (FR-PK-04)."""

    __tablename__ = "holidays"
    __table_args__ = _TABLE

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class LeadTimeDefaults(Base):
    """FR-PK-03: alert lead time when a calendar does not set its own. One row."""

    __tablename__ = "lead_time_defaults"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("monthly_days BETWEEN 0 AND 90", name="monthly_range"),
        CheckConstraint("quarterly_days BETWEEN 0 AND 90", name="quarterly_range"),
        CheckConstraint("yearly_days BETWEEN 0 AND 90", name="yearly_range"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    monthly_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    quarterly_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    yearly_days: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
