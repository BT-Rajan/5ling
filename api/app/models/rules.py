"""Rules an Administrator edits (BRD NFR-MN-01), and break-glass access (FR-AC-05).

The rules are one row (id 1), loaded by migration 0027 with the defaults and changed on the
Settings screen, so changing one needs no software release. Due dates, lead times, holidays and
packages were already data (C05, C06); these are the remaining timings.

A break-glass grant lets one Administrator open one client they do not own, for a limited time,
after giving a reason. Grants are never changed or deleted (triggers), and each is flagged in the
audit log.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

# (column, default, lowest, highest): the limits the API and the database both hold to.
RULE_LIMITS = {
    "waiting_grace_days": (3, 0, 30),  # FR-IN-02: after the first alert, "waiting on client"
    "reminder_interval_days": (2, 1, 14),  # FR-AL-03: days between reminders
    "escalation_days_before": (2, 0, 30),  # FR-IN-03: escalate empty cycles this close to due
    "portal_link_hours": (72, 1, 720),  # FR-AL-02: how long a client's link lasts
    "idle_timeout_minutes": (20, 5, 240),  # NFR-SE-03: sign Staff and Administrators out
    "break_glass_minutes": (240, 15, 1440),  # FR-AC-05: how long a break-glass grant lasts
    "fee_reminder_interval_days": (7, 1, 60),  # FR-FE-07: days between fee reminders
}


class RuleSettings(Base):
    __tablename__ = "rule_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        *(
            CheckConstraint(f"{col} BETWEEN {lo} AND {hi}", name=f"{col}_range")
            for col, (_, lo, hi) in RULE_LIMITS.items()
        ),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    waiting_grace_days: Mapped[int] = mapped_column(Integer, nullable=False)
    reminder_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    escalation_days_before: Mapped[int] = mapped_column(Integer, nullable=False)
    portal_link_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    idle_timeout_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    break_glass_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_reminder_interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class BreakGlassGrant(Base):
    __tablename__ = "break_glass_grants"
    __table_args__ = (
        Index(
            "ix_break_glass_grants_user_id_client_pk_expires_at",
            "user_id",
            "client_pk",
            "expires_at",
        ),
        CheckConstraint("expires_at > granted_at", name="expires_after_granted"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
