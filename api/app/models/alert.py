"""Automatic client alerts and reminders (BRD FR-AL-01, FR-AL-03).

One row per alert sent for a cycle, at most one a day (unique), so the job can run as often as
it likes without sending twice. Rows are only added (triggers, migration 0018).
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class AlertKind(enum.StrEnum):
    FIRST = "first"  # on the alert date (FR-AL-01)
    REMINDER = "reminder"  # every few days, then daily after the due date (FR-AL-03)


class CycleAlert(Base):
    __tablename__ = "cycle_alerts"
    __table_args__ = (UniqueConstraint("cycle_id", "sent_on"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=False)
    kind: Mapped[AlertKind] = mapped_column(
        Enum(AlertKind, values_callable=lambda e: [m.value for m in e], name="alert_kind"),
        nullable=False,
    )
    sent_on: Mapped[date] = mapped_column(Date, nullable=False)  # India date
    email_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("emails.id"), unique=True, nullable=False
    )
    portal_link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("portal_links.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
