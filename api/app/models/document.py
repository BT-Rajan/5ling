"""Documents and their versions (BRD FR-AL-04, FR-AL-05, section 10 "Document").

A document is one thing collected for a client's cycle (for example "Sales register"); each
upload is a new version and nothing is ever overwritten or deleted (triggers, migration 0016).
A version can be opened only once it has been scanned clean.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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


class ScanStatus(enum.StrEnum):
    PENDING = "pending"  # stored, waiting for the virus scanner: not shown to anyone
    CLEAN = "clean"
    INFECTED = "infected"  # found by a later scan: the stored file is destroyed, the record kept


class UploadedVia(enum.StrEnum):
    STAFF = "staff"
    PORTAL = "portal"  # the client, through a private link (chunk C17)
    CONSULTANT = "consultant"  # work from the person the cycle is assigned to (chunk C21)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (Index("ix_documents_cycle_id_id", "cycle_id", "id"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version"),
        Index("ix_document_versions_scan_status_id", "scan_status", "id"),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(150), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_via: Mapped[UploadedVia] = mapped_column(
        _enum(UploadedVia, "uploaded_via"), nullable=False
    )
    uploaded_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    portal_link_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("portal_links.id"), nullable=True
    )
    uploaded_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    scan_status: Mapped[ScanStatus] = mapped_column(
        _enum(ScanStatus, "scan_status"), nullable=False
    )
    scanned_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    scan_detail: Mapped[str | None] = mapped_column(String(150), nullable=True)


class CycleChecklistItem(Base):
    """One document asked of the client for one cycle (FR-AL-04), copied from the package
    version's checklist when the cycle is made, so later package changes leave it alone
    (FR-PK-05). Uploads for it collect in one document, as versions."""

    __tablename__ = "cycle_checklist_items"
    __table_args__ = (UniqueConstraint("cycle_id", "position"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cycles.id"), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("documents.id"), unique=True, nullable=True
    )
