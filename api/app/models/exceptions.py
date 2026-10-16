"""Per-client filing changes (BRD FR-PK-07), chunk C37.

A client normally gets exactly its package's filings. The owner or an Administrator may add a
filing for that client only (it applies at once) or ask to remove one (it waits for an
Administrator; the client keeps the filing meanwhile). An Administrator's own request applies at
once. Requests are never changed or deleted, and are decided once (triggers).
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class FilingChangeKind(enum.StrEnum):
    ADD = "add"
    REMOVE = "remove"


class FilingChangeStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


def _enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, values_callable=lambda e: [m.value for m in e], name=name)


class ClientFilingChange(Base):
    __tablename__ = "client_filing_changes"
    __table_args__ = (
        Index("ix_client_filing_changes_client_pk_id", "client_pk", "id"),
        Index("ix_client_filing_changes_status", "status"),
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)"
            " AND (decided_at IS NULL) = (decided_by_id IS NULL)",
            name="decided_with_whom",
        ),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_pk: Mapped[int] = mapped_column(BigInteger, ForeignKey("clients.id"), nullable=False)
    filing_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("filings.id"), nullable=False)
    kind: Mapped[FilingChangeKind] = mapped_column(
        _enum(FilingChangeKind, "change_kind"), nullable=False
    )
    status: Mapped[FilingChangeStatus] = mapped_column(
        _enum(FilingChangeStatus, "change_status"), nullable=False
    )
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    requested_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    decided_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id"), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
