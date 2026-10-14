"""Private client links (BRD FR-AL-02): one client, one cycle, expiring, opened once.

A link's token is 256 random bits, kept only as a SHA-256 hash. Opening the link uses it up and
starts a portal session (its own cookie, also kept only as a hash) for that one client and
cycle, until the link's expiry. A reused, expired or withdrawn link shows why, and the client
can ask for a new one, which goes to the contact address on file, not to whoever holds the link.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class LinkOrigin(enum.StrEnum):
    STAFF = "staff"  # made by a person on the ticket page
    CLIENT_REQUEST = "client_request"  # the client asked for a new one
    SYSTEM = "system"  # a scheduled alert (chunk C18)


class PortalLink(Base):
    __tablename__ = "portal_links"
    __table_args__ = (
        Index("ix_portal_links_cycle_id_id", "cycle_id", "id"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=False)
    origin: Mapped[LinkOrigin] = mapped_column(
        Enum(LinkOrigin, values_callable=lambda e: [m.value for m in e], name="origin"),
        nullable=False,
    )
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # Set when the link is opened: it cannot be opened again, and the session below begins.
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    session_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # The link the client asked from, when they asked for a new one (one request per link).
    requested_from_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("portal_links.id"), unique=True, nullable=True
    )
