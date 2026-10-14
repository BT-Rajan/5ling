"""Checking the audit trail (BRD FR-AU-03): on demand, daily, and with every export.

The check reads the whole trail in one consistent snapshot and, entry by entry, confirms the
numbering has no gaps, each entry points at the hash of the one before, and each hash matches a
fresh computation over the entry's stored fields. It then confirms the chain head names the last
entry, and that the entry a previous passing check recorded still has the hash it had then (so a
rewrite that recomputes every hash is caught as well).

Every run is stored in `audit_checks` and written to the trail itself. A failure is logged at
CRITICAL and shown to every Administrator (the BRD's "Partner or practice head") on every screen
until a later check passes. Email alerts follow once email exists (chunk C13).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Row, select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.chain import AuditRecord, entry_hash
from app.audit.writer import audit
from app.mail.outbox import alert_admins_audit_failed
from app.models import GENESIS_HASH, AuditChain, AuditCheck, AuditEntry, CheckTrigger
from app.timeutil import utcnow

log = logging.getLogger("ledgerline.audit")

BATCH = 5000

PROBLEMS = {
    "gap": "An entry is missing: the numbering skips here.",
    "broken_link": "This entry does not point at the one before it.",
    "hash_mismatch": "This entry was changed after it was written.",
    "anchor_mismatch": "This entry differs from what an earlier check recorded.",
    "entries_missing": "Entries an earlier check saw are gone.",
    "head_missing": "The record of the newest entry is missing.",
    "head_mismatch": "The record of the newest entry does not match the trail.",
}

_COLUMNS = (
    AuditEntry.seq,
    AuditEntry.occurred_at,
    AuditEntry.event,
    AuditEntry.actor_user_id,
    AuditEntry.actor_role,
    AuditEntry.ip,
    AuditEntry.request_id,
    AuditEntry.target_type,
    AuditEntry.target_id,
    AuditEntry.details,
    AuditEntry.prev_hash,
    AuditEntry.entry_hash,
)


@dataclass(frozen=True)
class ChainResult:
    ok: bool
    entries_checked: int
    last_seq: int  # the newest entry that checked out
    last_hash: str
    first_bad_seq: int | None = None
    problem: str | None = None


def _recomputed(row: Row[Any], prev_hash: str) -> str:
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
    return entry_hash(row.seq, prev_hash, record)


def verify_chain(db: Session, anchor: tuple[int, str] | None = None) -> ChainResult:
    """Check the whole trail as `db` sees it. Call inside a REPEATABLE READ transaction."""
    head = db.execute(
        select(AuditChain.last_seq, AuditChain.last_hash).where(AuditChain.id == 1)
    ).one_or_none()
    prev, expected, checked = GENESIS_HASH, 1, 0

    def failed(problem: str, seq: int | None) -> ChainResult:
        return ChainResult(False, checked, expected - 1, prev, seq, problem)

    after = 0
    while True:
        rows = db.execute(
            select(*_COLUMNS).where(AuditEntry.seq > after).order_by(AuditEntry.seq).limit(BATCH)
        ).all()
        if not rows:
            break
        for row in rows:
            if row.seq != expected:
                return failed("gap", expected)
            if row.prev_hash != prev:
                return failed("broken_link", row.seq)
            if _recomputed(row, prev) != row.entry_hash:
                return failed("hash_mismatch", row.seq)
            if anchor is not None and row.seq == anchor[0] and row.entry_hash != anchor[1]:
                return failed("anchor_mismatch", row.seq)
            prev, expected, checked = row.entry_hash, expected + 1, checked + 1
        after = rows[-1].seq

    if anchor is not None and anchor[0] >= expected:
        return failed("entries_missing", expected)
    if head is None:
        return failed("head_missing", None)
    if (int(head.last_seq), str(head.last_hash)) != (expected - 1, prev):
        return failed("head_mismatch", expected)
    return ChainResult(True, checked, expected - 1, prev)


def run_check(
    factory: sessionmaker[Session],
    trigger: CheckTrigger,
    *,
    requested_by_id: int | None = None,
    firm_name: str = "Ledgerline",
) -> AuditCheck:
    """Verify the trail, store the result, record it in the trail, and raise the alarm if bad.

    A failure also emails every Administrator (at most once a day), in the same transaction.
    """
    started = utcnow()
    with factory() as db:
        # One snapshot for the head, the anchor and every entry, while writers carry on.
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        anchor_row = db.execute(
            select(AuditCheck.last_seq, AuditCheck.last_hash)
            .where(AuditCheck.ok.is_(True))
            .order_by(AuditCheck.id.desc())
            .limit(1)
        ).one_or_none()
        anchor = (int(anchor_row[0]), str(anchor_row[1])) if anchor_row else None
        result = verify_chain(db, anchor)
        db.rollback()

        check = AuditCheck(
            started_at=started,
            finished_at=utcnow(),
            trigger=trigger,
            requested_by_id=requested_by_id,
            ok=result.ok,
            entries_checked=result.entries_checked,
            last_seq=result.last_seq,
            last_hash=result.last_hash,
            first_bad_seq=result.first_bad_seq,
            problem=result.problem,
        )
        db.add(check)
        audit(
            db,
            "audit.check_passed" if result.ok else "audit.check_failed",
            actor_user_id=requested_by_id,
            actor_role="administrator" if requested_by_id is not None else None,
            details={
                "trigger": trigger,
                "entries_checked": result.entries_checked,
                "last_seq": result.last_seq,
                "first_bad_seq": result.first_bad_seq,
                "problem": result.problem,
            },
        )
        if not result.ok:
            where = f" at entry {result.first_bad_seq}" if result.first_bad_seq else ""
            alert_admins_audit_failed(
                db,
                f"Problem{where}: {PROBLEMS.get(result.problem or '', result.problem)}.",
                firm_name,
            )
        db.commit()

    facts: dict[str, object] = {
        "event": "audit.check_failed" if not result.ok else "audit.check_passed",
        "trigger": trigger.value,
        "entries_checked": result.entries_checked,
        "last_seq": result.last_seq,
        # Shipping the newest verified hash to the log gives an anchor outside the database.
        "last_hash": result.last_hash,
    }
    if result.ok:
        log.info("audit chain check passed", extra={"ctx": facts})
    else:
        facts |= {"first_bad_seq": result.first_bad_seq, "problem": result.problem}
        log.critical("AUDIT CHAIN CHECK FAILED", extra={"ctx": facts})
    return check
