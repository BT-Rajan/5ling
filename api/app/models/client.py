"""Clients, their contacts and channel consent (BRD 7.1, CR-01, FR-AL-08).

The client ID people see is a 6-digit number from `client_id_counter`, an AUTO_INCREMENT table
used only for this: InnoDB never hands out the same value twice, so an ID is never reused, even
after a failed save. Triggers (migration 0008) refuse any change to a client's ID, the deletion
of a client, and any change to the consent log.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

FIRST_CLIENT_ID = 100001
LAST_CLIENT_ID = 999999


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, values_callable=lambda e: [m.value for m in e], name=name)


class ClientKind(enum.StrEnum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class ClientStatus(enum.StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class Channel(enum.StrEnum):
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    SMS = "sms"


class ClientIdCounter(Base):
    """One row per client ID ever handed out (CR-01). Rows are only added."""

    __tablename__ = "client_id_counter"
    __table_args__ = {**_TABLE, "mysql_auto_increment": str(FIRST_CLIENT_ID)}  # noqa: RUF012

    client_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint(
            f"client_id BETWEEN {FIRST_CLIENT_ID} AND {LAST_CLIENT_ID}", name="six_digits"
        ),
        Index("ix_clients_owner_id_status", "owner_id", "status"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    kind: Mapped[ClientKind] = mapped_column(_enum(ClientKind, "client_kind"), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    # Unique so a second client with the same PAN or GSTIN cannot be saved (FR-CL-02).
    pan: Mapped[str | None] = mapped_column(String(10), unique=True, nullable=True)
    gstin: Mapped[str | None] = mapped_column(String(15), unique=True, nullable=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    package_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("packages.id"), nullable=False)
    # The package version the client was given; cycles are built from it (FR-PK-05).
    package_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("package_versions.id"), nullable=False
    )
    status: Mapped[ClientStatus] = mapped_column(
        _enum(ClientStatus, "client_status"), nullable=False
    )
    created_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class Contact(Base):
    """A person at the client. Exactly one per client is the primary recipient (FR-CL-03)."""

    __tablename__ = "client_contacts"
    __table_args__ = _TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("clients.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(254), nullable=False)
    phone: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # E.164, e.g. +919876543210
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False)
    position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ConsentChange(Base):
    """FR-AL-08, BRD s12: each change to a contact's channel consent, kept forever (append-only).

    The newest row per contact and channel is the current state. Email is the minimum channel
    and is always on; WhatsApp and SMS start off.
    """

    __tablename__ = "contact_consents"
    __table_args__ = (
        Index("ix_contact_consents_contact_id_channel", "contact_id", "channel"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    contact_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("client_contacts.id"), nullable=False
    )
    channel: Mapped[Channel] = mapped_column(_enum(Channel, "channel"), nullable=False)
    granted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    changed_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ChangeField(enum.StrEnum):
    OWNER = "owner"
    PACKAGE = "package"
    STATUS = "status"


class ClientChange(Base):
    """FR-CL-04: each change of owner, package or status, dated, attributed and with a reason.

    Append-only (triggers, migration 0009). Values are stored as text: user ids for owner,
    package version ids for package, the status name for status.
    """

    __tablename__ = "client_changes"
    __table_args__ = (Index("ix_client_changes_client_pk_id", "client_pk", "id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    field: Mapped[ChangeField] = mapped_column(_enum(ChangeField, "change_field"), nullable=False)
    old_value: Mapped[str] = mapped_column(String(64), nullable=False)
    new_value: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    changed_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
