"""Nil filings (FR-CY-03) and phone follow-up (FR-IN-05), chunk C34.

A nil confirmation is the client's statement, made on the portal, that a period had no activity.
It is kept as given (never changed or deleted) and shown to the reviewer.

A phone contact is a staff member's note that they rang the client, with the date the client
promised (to send documents, say). On that date the staff member is reminded if the work is still
waiting. Notes are never changed or deleted; only the reminder time is set, once.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class NilConfirmation(Base):
    __tablename__ = "cycle_nil_confirmations"
    __table_args__ = (_TABLE,)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cycles.id"), unique=True, nullable=False
    )
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    portal_link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("portal_links.id"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    confirmed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class PhoneContact(Base):
    __tablename__ = "phone_contacts"
    __table_args__ = (
        Index("ix_phone_contacts_ticket_id_id", "ticket_id", "id"),
        Index("ix_phone_contacts_due", "reminded_at", "promised_on"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticket_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("tickets.id"), nullable=False)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    promised_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # Set once, when the promised date has been dealt with: reminded, or no longer needed.
    reminded_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reminder_sent: Mapped[bool | None] = mapped_column(nullable=True)


class ClientMessage(Base):
    """A message the client wrote on the portal, kept against the cycle (FR-AL-07). Never changed
    or deleted; read by staff on the ticket."""

    __tablename__ = "client_messages"
    __table_args__ = (Index("ix_client_messages_cycle_id_id", "cycle_id", "id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=False)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    portal_link_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("portal_links.id"), nullable=False
    )
    body: Mapped[str] = mapped_column(String(2000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
