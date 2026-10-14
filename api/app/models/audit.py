"""The audit trail (BRD FR-AU-01 to FR-AU-03). Rows are only ever added.

The database itself refuses UPDATE and DELETE on `audit_entries` (triggers, see migration 0004),
and refuses an INSERT that does not extend the chain held in `audit_chain`. The running app's
database account has no DROP, ALTER or TRIGGER rights, so it can neither truncate the table nor
remove the triggers. Nothing here has a foreign key: a foreign-key cascade would change rows
without firing triggers, and the trail must outlive whatever it describes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, Index, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

GENESIS_HASH = "0" * 64


class AuditEntry(Base):
    __tablename__ = "audit_entries"
    __table_args__ = (
        Index("ix_audit_entries_occurred_at", "occurred_at"),
        Index("ix_audit_entries_actor_user_id_occurred_at", "actor_user_id", "occurred_at"),
        Index("ix_audit_entries_event_occurred_at", "event", "occurred_at"),
        Index("ix_audit_entries_target_type_target_id", "target_type", "target_id"),
        _TABLE,
    )

    # Position in the chain: 1, 2, 3 ... with no gaps (not AUTO_INCREMENT, which can skip).
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AuditChain(Base):
    """The single row naming the newest entry. Writers lock it, so the chain never forks."""

    __tablename__ = "audit_chain"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"), _TABLE)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_hash: Mapped[str] = mapped_column(String(64), nullable=False)
