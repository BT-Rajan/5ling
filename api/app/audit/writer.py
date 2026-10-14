"""Writing to the audit trail (BRD FR-AU-01, FR-AU-03).

Two ways in:

- `audit(db, ...)` queues an entry on the caller's session. It is written by that session's
  commit, in the same transaction, so the change and its record are saved together or not at
  all. Use it for every action that changes something.
- `write_now(factory, ...)` writes in a transaction of its own. Use it for things that must be
  kept even though the caller's work is being thrown away: failed sign-ins, refused requests.

Both append through `append`, which locks the single `audit_chain` row, so entries get
consecutive numbers and each one hashes the one before it. A session's queue is only written at
the very end of its commit, so that lock is held for milliseconds and never while any other code
runs (which is also why a request cannot deadlock against its own `write_now`).
"""

from __future__ import annotations

import enum
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import event, insert, select
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from app.audit.chain import AuditRecord, JsonScalar, canonical, entry_hash
from app.audit.context import current
from app.logging_conf import redact
from app.models import AuditChain, AuditEntry
from app.timeutil import utcnow

log = logging.getLogger("ledgerline.audit")

_QUEUE_KEY = "ll_audit_queue"
MAX_DETAIL_KEYS = 32
MAX_DETAIL_TEXT = 256
MAX_DETAIL_LIST = 20
MAX_DETAILS_BYTES = 4096
_INT64 = (-(2**63), 2**63 - 1)


class AuditChainMissing(RuntimeError):
    """The audit_chain head row is gone. Writing stops rather than starting a second chain."""


def _scalar(value: object) -> JsonScalar:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, enum.Enum):
        value = value.value
    if isinstance(value, int) and _INT64[0] <= value <= _INT64[1]:
        return value
    # Strings, floats (JSON numbers would not hash the same after MySQL re-encodes them), and
    # anything else become redacted, bounded text: the trail is no place for PAN or emails.
    return redact(str(value))[:MAX_DETAIL_TEXT]


def clean_details(details: Mapping[str, object] | None) -> dict[str, Any]:
    if not details:
        return {}
    out: dict[str, Any] = {}
    for key, value in list(details.items())[:MAX_DETAIL_KEYS]:
        name = str(key)[:64]
        if isinstance(value, list | tuple | set | frozenset):
            items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
            out[name] = [_scalar(v) for v in list(items)[:MAX_DETAIL_LIST]]
        else:
            out[name] = _scalar(value)
    if len(canonical(out)) > MAX_DETAILS_BYTES:
        return {"truncated": True}
    return out


def _bounded(value: object, limit: int) -> str | None:
    return None if value is None else str(value)[:limit]


def make_record(
    event: str,
    *,
    actor_user_id: int | None = None,
    actor_role: str | None = None,
    target: tuple[str, object] | None = None,
    details: Mapping[str, object] | None = None,
    ip: str | None = None,
    request_id: str | None = None,
) -> AuditRecord:
    if not event or len(event) > 64:
        raise ValueError("audit event names are 1 to 64 characters")
    ctx = current()
    if ctx is not None:
        ip = ip or ctx.ip
        request_id = request_id or ctx.request_id
    return AuditRecord(
        occurred_at=utcnow(),
        event=event,
        actor_user_id=actor_user_id,
        actor_role=_bounded(actor_role, 16),
        ip=_bounded(ip, 45),
        request_id=_bounded(request_id, 64),
        target_type=_bounded(target[0], 40) if target else None,
        target_id=_bounded(target[1], 64) if target else None,
        details=clean_details(details),
    )


def append(session: Session, records: Sequence[AuditRecord]) -> int:
    """Add records to the chain inside `session`'s transaction. Returns the newest number."""
    head = session.execute(
        select(AuditChain.last_seq, AuditChain.last_hash)
        .where(AuditChain.id == 1)
        .with_for_update()
    ).one_or_none()
    if head is None:
        raise AuditChainMissing("audit_chain has no head row")
    seq, prev = int(head[0]), str(head[1])
    if not records:
        return seq
    rows: list[dict[str, Any]] = []
    for record in records:
        seq += 1
        digest = entry_hash(seq, prev, record)
        rows.append(
            {
                "seq": seq,
                "occurred_at": record.occurred_at,
                "event": record.event,
                "actor_user_id": record.actor_user_id,
                "actor_role": record.actor_role,
                "ip": record.ip,
                "request_id": record.request_id,
                "target_type": record.target_type,
                "target_id": record.target_id,
                "details": record.details,
                "prev_hash": prev,
                "entry_hash": digest,
            }
        )
        prev = digest
    session.execute(insert(AuditEntry), rows)
    return seq


def queue(db: Session, record: AuditRecord) -> None:
    if not db.in_transaction():
        db.begin()  # so a rollback or close without commit is seen, and drops the entry
    db.info.setdefault(_QUEUE_KEY, []).append(record)
    ctx = current()
    if ctx is not None:
        ctx.recorded = True


def audit(db: Session, event: str, **fields: Any) -> None:
    """Record `event` as part of `db`'s current transaction (written when it commits)."""
    queue(db, make_record(event, **fields))


def write_now(factory: sessionmaker[Session], records: Sequence[AuditRecord]) -> None:
    """Write records in their own transaction, independent of whatever the caller is doing."""
    ctx = current()
    if ctx is not None:
        ctx.recorded = True
    with factory() as session:
        append(session, records)
        session.commit()


@event.listens_for(Session, "before_commit")
def _write_queued(session: Session) -> None:
    pending: list[AuditRecord] | None = session.info.pop(_QUEUE_KEY, None)
    if pending:
        append(session, pending)


@event.listens_for(Session, "after_transaction_end")
def _drop_unwritten(session: Session, transaction: SessionTransaction) -> None:
    # Rolled back (or closed without committing): the work did not happen, so neither did the
    # entries describing it.
    if transaction.parent is None:
        session.info.pop(_QUEUE_KEY, None)
