"""Monitoring (BRD NFR-OB-01): server errors counted per minute, and the alerts sent about
failed jobs, failed emails and error spikes. An alert is sent once per key (unique), so the
monitor can run every minute without repeating itself.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class ErrorMinute(Base):
    __tablename__ = "error_minutes"
    __table_args__ = (_TABLE,)

    minute: Mapped[datetime] = mapped_column(UTCDateTime, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class OpsAlertKind(enum.StrEnum):
    JOB_FAILED = "job_failed"  # a job run recorded as failed
    JOB_MISSED = "job_missed"  # the daily cycle generator has not run
    UNIT_FAILED = "unit_failed"  # a systemd service failed (OnFailure hook)
    EMAILS_FAILED = "emails_failed"  # client emails failed or bounced
    ERROR_SPIKE = "error_spike"  # server errors above the threshold


class OpsAlert(Base):
    __tablename__ = "ops_alerts"
    __table_args__ = (UniqueConstraint("kind", "dedupe_key"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[OpsAlertKind] = mapped_column(
        Enum(OpsAlertKind, values_callable=lambda e: [m.value for m in e], name="ops_alert_kind"),
        nullable=False,
    )
    dedupe_key: Mapped[str] = mapped_column(String(120), nullable=False)
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    detail: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
