"""WhatsApp and SMS messages (BRD INT-02, FR-AL-08, C-2), chunk C36.

Each row is one message on the second channel, sent alongside an email alert (email always goes:
it is the minimum channel). It is queued in the alert's transaction and sent by the alerts job.
Rows are never deleted, and once a message is sent, failed or skipped it never changes (trigger).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Enum, ForeignKey, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.models.client import Channel
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class MessageStatus(enum.StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    FAILED = "failed"  # the owner is told; the email went anyway
    SKIPPED = "skipped"  # consent withdrawn or the number removed before it was sent


class ChannelMessage(Base):
    __tablename__ = "channel_messages"
    __table_args__ = (
        Index("ix_channel_messages_status_id", "status", "id"),
        Index("ix_channel_messages_cycle_id", "cycle_id"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    cycle_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("client_contacts.id"), nullable=False
    )
    email_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("emails.id"), nullable=True
    )  # the email sent with it: the client has that whatever happens here
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, name="message_channel", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    template: Mapped[str] = mapped_column(String(40), nullable=False)
    body_text: Mapped[str] = mapped_column(String(1000), nullable=False)
    params: Mapped[list[str]] = mapped_column(JSON, nullable=False)  # the template's values
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, name="message_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    provider_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
