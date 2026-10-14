"""Tickets and filing cycles (BRD 7.3, CR-02, CR-03).

Every task is a ticket with an ID like TKT-000001, from `ticket_counter` (AUTO_INCREMENT, so an
ID is never reused). A FILING ticket has exactly one cycle: one filing for one period for one
client, built from a recorded package version and calendar version (FR-PK-05). Its stage moves
only along the transitions in `app.cycles.stages`.

Stage history, ticket IDs and state events are append-only (triggers, migration 0010).
"""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
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


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, values_callable=lambda e: [m.value for m in e], name=name)


class TicketType(enum.StrEnum):
    FILING = "filing"  # made with its cycle
    TASK = "task"  # ad hoc work that is not a filing (chunk C11)


class Stage(enum.StrEnum):
    """FR-CY-02, in order."""

    SCHEDULED = "scheduled"
    AWAITING_DOCUMENTS = "awaiting_documents"
    DOCUMENTS_RECEIVED = "documents_received"
    ASSIGNED = "assigned"
    PREPARING = "preparing"
    IN_REVIEW = "in_review"
    READY_TO_FILE = "ready_to_file"
    FILED = "filed"
    ACKNOWLEDGED = "acknowledged"
    CLOSED = "closed"


class TicketCounter(Base):
    """One row per ticket number ever handed out (CR-02). Rows are only added."""

    __tablename__ = "ticket_counter"
    __table_args__ = _TABLE

    number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (Index("ix_tickets_client_pk_number", "client_pk", "number"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    type: Mapped[TicketType] = mapped_column(_enum(TicketType, "ticket_type"), nullable=False)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )  # None: made by the system (the cycle generator)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Cycle(Base):
    __tablename__ = "cycles"
    __table_args__ = (
        # FR-CY-04: never the same filing for the same period for the same client twice.
        UniqueConstraint("client_pk", "filing_id", "period_start"),
        Index("ix_cycles_stage_due_date", "stage", "due_date"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tickets.id"), unique=True, nullable=False
    )
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id"), nullable=False)
    package_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("package_versions.id"), nullable=False
    )
    calendar_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("calendar_versions.id"), nullable=False
    )
    period_key: Mapped[str] = mapped_column(String(8), nullable=False)
    period_label: Mapped[str] = mapped_column(String(40), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    alert_date: Mapped[date] = mapped_column(Date, nullable=False)
    stage: Mapped[Stage] = mapped_column(_enum(Stage, "stage"), nullable=False)
    stage_changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class StageChange(Base):
    """Each move of a cycle's stage, by whom and why. Append-only."""

    __tablename__ = "cycle_stage_changes"
    __table_args__ = (Index("ix_cycle_stage_changes_cycle_id_id", "cycle_id", "id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=False)
    from_stage: Mapped[Stage | None] = mapped_column(_enum(Stage, "from_stage"), nullable=True)
    to_stage: Mapped[Stage] = mapped_column(_enum(Stage, "to_stage"), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    changed_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class EventKind(enum.StrEnum):
    CYCLE_STAGE = "cycle_stage"
    CLIENT_STATUS = "client_status"
    TASK_STATUS = "task_status"


class StateEvent(Base):
    """CR-03: every state change, waiting to become a draft email to the client (chunk C15).

    Written in the same transaction as the change, so none is lost. Append-only, except that C15
    marks each one handled.
    """

    __tablename__ = "state_events"
    __table_args__ = (Index("ix_state_events_handled_at_id", "handled_at", "id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[EventKind] = mapped_column(_enum(EventKind, "event_kind"), nullable=False)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    ticket_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tickets.id"), nullable=True
    )
    from_state: Mapped[str] = mapped_column(String(40), nullable=False)
    to_state: Mapped[str] = mapped_column(String(40), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    handled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class TaskStatus(enum.StrEnum):
    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"


class Task(Base):
    """The details of a TASK ticket (CR-02): ad hoc work that is not a filing."""

    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_assignee_id_status_due_date", "assignee_id", "status", "due_date"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tickets.id"), unique=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    assignee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus, "task_status"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
