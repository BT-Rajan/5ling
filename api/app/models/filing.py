"""Proof that a cycle was filed (BRD FR-WF-05).

A cycle reaches Filed only with an uploaded acknowledgement and the reference number the
government portal gave. One row per cycle, written in the same transaction as the move to Filed;
it is never changed or deleted, and the database refuses to move a cycle to Filed without it
(triggers, migration 0021).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class CycleFiling(Base):
    __tablename__ = "cycle_filings"
    __table_args__ = (_TABLE,)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    cycle_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("cycles.id"), nullable=False, unique=True
    )
    reference: Mapped[str] = mapped_column(String(50), nullable=False)
    acknowledgement_version_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("document_versions.id"), nullable=False
    )
    filed_by_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    filed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    # FR-RP-03: why it was filed after its due date (required then; migration 0026).
    late_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
