"""The email outbox (BRD INT-01, FR-AL-06, CR-03).

Every email the system sends is a row here first. Its status only moves forward:

    draft -> queued -> sent -> delivered
                    \\-> failed   \\-> bounced (or failed, if the provider reports it)

Once an email leaves draft, who it goes to and what it says are final (triggers, migration
0014), and rows are never deleted. Each status change is kept in `email_events` (append-only).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
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


class EmailStatus(enum.StrEnum):
    DRAFT = "draft"  # written, waiting for someone to press Send (CR-03)
    QUEUED = "queued"  # waiting for the send worker, or for its next try
    SENT = "sent"  # accepted by the email provider
    DELIVERED = "delivered"  # the provider reported delivery
    FAILED = "failed"  # refused, or still not sent after 24 hours of trying
    BOUNCED = "bounced"  # the recipient's server sent it back


class EmailKind(enum.StrEnum):
    TEST = "test"  # sent by an Administrator to check the setup
    AUDIT_ALERT = "audit_alert"  # the audit log check failed (FR-AU-03)
    CLIENT_UPDATE = "client_update"  # a state change, sent by hand (CR-03, chunk C15)


class EventSource(enum.StrEnum):
    APP = "app"  # someone, or the app itself, created or queued it
    WORKER = "worker"  # the send worker
    PROVIDER = "provider"  # a delivery report from the email provider


class Email(Base):
    __tablename__ = "emails"
    __table_args__ = (
        Index("ix_emails_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_emails_client_pk_id", "client_pk", "id"),
        Index("ix_emails_ticket_id_id", "ticket_id", "id"),
        Index("ix_emails_kind_created_at", "kind", "created_at"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Random, in the Message-ID header and delivery reports; never the row number.
    public_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    kind: Mapped[EmailKind] = mapped_column(_enum(EmailKind, "email_kind"), nullable=False)
    status: Mapped[EmailStatus] = mapped_column(_enum(EmailStatus, "email_status"), nullable=False)
    to_addresses: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    cc_addresses: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    subject: Mapped[str] = mapped_column(String(250), nullable=False)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    client_pk: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("clients.id"), nullable=True
    )
    ticket_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tickets.id"), nullable=True
    )
    # The state change a client update was drafted for (CR-03): at most one email each.
    state_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("state_events.id"), unique=True, nullable=True
    )
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )  # None: made by the system
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    queued_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    queued_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(250), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class EmailEvent(Base):
    """One step in an email's life. Append-only."""

    __tablename__ = "email_events"
    __table_args__ = (Index("ix_email_events_email_id_id", "email_id", "id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("emails.id"), nullable=False)
    status: Mapped[EmailStatus] = mapped_column(_enum(EmailStatus, "event_status"), nullable=False)
    source: Mapped[EventSource] = mapped_column(_enum(EventSource, "event_source"), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
