"""Who is working on a cycle (BRD FR-WF-01, FR-WF-02).

A cycle is assigned to one in-house person or one external consultant at a time, with a date
the work is due and a note. That person gets access to that one cycle only, until the assignment
ends: withdrawn, replaced by a new assignment, or the cycle closed. The database allows at most
one open assignment per cycle (a unique key on a computed column); rows are never deleted and
an ended assignment stays ended (triggers, migration 0020).
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import BigInteger, Computed, Date, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class EndReason(enum.StrEnum):
    WITHDRAWN = "withdrawn"
    REASSIGNED = "reassigned"
    CYCLE_CLOSED = "cycle_closed"


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        Index("ix_assignments_assignee_id_ended_at", "assignee_id", "ended_at"),
        Index("ix_assignments_cycle_id_id", "cycle_id", "id"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=False)
    assignee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    work_due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    assigned_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ended_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    end_reason: Mapped[EndReason | None] = mapped_column(
        Enum(EndReason, values_callable=lambda e: [m.value for m in e], name="end_reason"),
        nullable=True,
    )
    end_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    # The cycle while the assignment is open, else NULL: unique, so one open assignment a cycle.
    open_cycle_id: Mapped[int | None] = mapped_column(
        BigInteger,
        Computed("(if(`ended_at` is null,`cycle_id`,NULL))", persisted=True),
        unique=True,
    )
