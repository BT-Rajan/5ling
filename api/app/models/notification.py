"""In-app notifications (BRD FR-IN-01 to FR-IN-03): who needs to know what, about which cycle.

A notification goes to one person (`user_id`) or to everyone in a role (`for_role`, used for
escalations to the Administrators: one shared record, so it is created once and dismissed once).
`dedupe_key` is unique, so a job can run any number of times without repeating one. Rows are
never deleted; dismissing is recorded with who, when and (for escalations, always) a note
(triggers, migration 0019).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.models.user import Role
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class NotificationKind(enum.StrEnum):
    DOCUMENTS_RECEIVED = "documents_received"  # FR-IN-01
    WAITING_ON_CLIENT = "waiting_on_client"  # FR-IN-02
    ESCALATION = "escalation"  # FR-IN-03: needs a note to dismiss


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_id_dismissed_at_id", "user_id", "dismissed_at", "id"),
        Index("ix_notifications_for_role_dismissed_at_id", "for_role", "dismissed_at", "id"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[NotificationKind] = mapped_column(
        Enum(NotificationKind, values_callable=lambda e: [m.value for m in e], name="kind"),
        nullable=False,
    )
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    for_role: Mapped[Role | None] = mapped_column(
        Enum(Role, values_callable=lambda e: [m.value for m in e], name="for_role"),
        nullable=True,
    )
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    dismissed_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    dismiss_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
