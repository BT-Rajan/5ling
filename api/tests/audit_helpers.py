"""Read the audit trail back and check it the way chunk C04 will: numbering and hashes."""

from __future__ import annotations

from app.audit import AuditRecord, entry_hash
from app.models import GENESIS_HASH, AuditEntry
from sqlalchemy import select

from tests.helpers import db_session


def entries(url: str) -> list[AuditEntry]:
    with db_session(url) as db:
        return list(db.scalars(select(AuditEntry).order_by(AuditEntry.seq)))


def events(url: str) -> list[str]:
    return [e.event for e in entries(url)]


def assert_chain_intact(rows: list[AuditEntry]) -> None:
    prev = GENESIS_HASH
    for expected_seq, row in enumerate(rows, start=1):
        assert row.seq == expected_seq, f"gap or reorder at {row.seq}"
        assert row.prev_hash == prev, f"broken link at {row.seq}"
        record = AuditRecord(
            occurred_at=row.occurred_at,
            event=row.event,
            actor_user_id=row.actor_user_id,
            actor_role=row.actor_role,
            ip=row.ip,
            request_id=row.request_id,
            target_type=row.target_type,
            target_id=row.target_id,
            details=row.details,
        )
        assert entry_hash(row.seq, prev, record) == row.entry_hash, f"bad hash at {row.seq}"
        prev = row.entry_hash
