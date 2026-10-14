"""Background job runs, so a failure is seen the same day and a run can be repeated (FR-CY-05)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, Integer, String
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


class JobTrigger(enum.StrEnum):
    SCHEDULED = "scheduled"  # the daily timer
    MANUAL = "manual"  # an Administrator pressed "Run again"
    CLIENT_CREATED = "client_created"
    PACKAGE_CHANGED = "package_changed"


class JobStatus(enum.StrEnum):
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"  # at least one client could not be done; re-running is safe
    SKIPPED = "skipped"  # another run was already going


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_job_runs_job_id", "job", "id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(40), nullable=False)
    trigger: Mapped[JobTrigger] = mapped_column(_enum(JobTrigger, "job_trigger"), nullable=False)
    requested_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, "job_status"), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    clients_checked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clients_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cycles_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # What went wrong, by client ID and error type only: never personal data.
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
