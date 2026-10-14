"""Chunk C04: the chain check catches tampering and raises the alarm (BRD FR-AU-03).

Tampering is done the way a real attacker with the database owner's account would have to: drop
the triggers, change rows, then put the triggers back. The test database is the "test copy" the
BRD's acceptance condition speaks of.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC
from pathlib import Path
from typing import Any

import pytest
from app.audit import audit
from app.audit.chain import AuditRecord, entry_hash
from app.audit.verify import run_check, verify_chain
from app.cli import main
from app.config import get_settings
from app.db import make_engine, make_session_factory
from app.models import GENESIS_HASH, AuditCheck, CheckTrigger, Role
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from tests.audit_helpers import entries, events
from tests.conftest import GOOD_SECRET
from tests.helpers import db_session, make_user, sign_in

pytestmark = pytest.mark.requires_db

_VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _triggers() -> dict[str, str]:
    found: dict[str, str] = {}
    for name in ("0004_audit_log.py", "0005_audit_checks.py"):
        spec = importlib.util.spec_from_file_location(f"m{name[:4]}", _VERSIONS / name)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        found.update(module._TRIGGERS)
    return found


@contextmanager
def tampering(url: str) -> Iterator[Any]:
    """An owner-account connection with the protective triggers removed, restored afterwards."""
    triggers = _triggers()
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            for name in triggers:
                conn.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
        with engine.begin() as conn:
            yield conn
    finally:
        with engine.begin() as conn:
            for name, ddl in triggers.items():
                conn.execute(text(f"DROP TRIGGER IF EXISTS {name}"))
                conn.execute(text(ddl))
        engine.dispose()


def _write(url: str, n: int) -> None:
    with db_session(url) as db:
        for i in range(n):
            audit(db, "test.event", actor_user_id=7, details={"i": i})
        db.commit()


def _check(url: str, trigger: CheckTrigger = CheckTrigger.MANUAL) -> AuditCheck:
    engine = make_engine(url)
    try:
        return run_check(make_session_factory(engine), trigger)
    finally:
        engine.dispose()


def _verify(url: str, anchor: tuple[int, str] | None = None):  # type: ignore[no-untyped-def]
    with db_session(url) as db:
        return verify_chain(db, anchor)


# --- Done when: altering a row in a test copy fails the check and raises an alert -----------


def test_altering_a_stored_entry_fails_the_check_and_raises_the_alarm(
    web: TestClient, migrated_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    admin = sign_in(web, "boss@firm.in")
    _write(migrated_db, 3)
    first = web.post("/api/admin/audit/verify", headers=admin.headers)
    assert first.status_code == 200
    assert first.json()["ok"] is True
    assert web.get("/api/admin/audit/status").json()["latest"]["ok"] is True

    target = next(e.seq for e in entries(migrated_db) if e.event == "test.event")
    with tampering(migrated_db) as conn:
        conn.execute(
            text("UPDATE audit_entries SET details = JSON_OBJECT('i', 99) WHERE seq = :s"),
            {"s": target},
        )

    with caplog.at_level(logging.CRITICAL, logger="ledgerline.audit"):
        check = _check(migrated_db, CheckTrigger.SCHEDULED)
    assert (check.ok, check.problem, check.first_bad_seq) == (False, "hash_mismatch", target)
    # The alarm: a CRITICAL log line for monitoring, and every Administrator's screen.
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    latest = web.get("/api/admin/audit/status").json()["latest"]
    assert latest["ok"] is False
    assert latest["first_bad_seq"] == target
    assert latest["problem_text"] == "This entry was changed after it was written."
    # The failure is itself on the record (appended after the damage).
    assert events(migrated_db)[-1] == "audit.check_failed"


# --- What each kind of tampering looks like --------------------------------------------------


def test_an_untouched_trail_passes(migrated_db: str) -> None:
    assert _verify(migrated_db).ok  # empty
    _write(migrated_db, 12)
    result = _verify(migrated_db)
    assert (result.ok, result.entries_checked, result.last_seq) == (True, 12, 12)
    assert result.last_hash == entries(migrated_db)[-1].entry_hash


def test_a_removed_entry_is_a_gap(migrated_db: str) -> None:
    _write(migrated_db, 5)
    with tampering(migrated_db) as conn:
        conn.execute(text("DELETE FROM audit_entries WHERE seq = 3"))
    result = _verify(migrated_db)
    assert (result.ok, result.problem, result.first_bad_seq, result.last_seq) == (
        False,
        "gap",
        3,
        2,
    )


def test_a_repointed_entry_is_a_broken_link(migrated_db: str) -> None:
    _write(migrated_db, 4)
    with tampering(migrated_db) as conn:
        conn.execute(text("UPDATE audit_entries SET prev_hash = REPEAT('a', 64) WHERE seq = 3"))
    result = _verify(migrated_db)
    assert (result.problem, result.first_bad_seq) == ("broken_link", 3)


def test_changing_the_time_or_actor_is_caught(migrated_db: str) -> None:
    _write(migrated_db, 3)
    with tampering(migrated_db) as conn:
        conn.execute(text("UPDATE audit_entries SET actor_user_id = 8 WHERE seq = 1"))
    assert _verify(migrated_db).problem == "hash_mismatch"


def test_an_entry_slipped_in_at_the_end_does_not_match_the_head(migrated_db: str) -> None:
    _write(migrated_db, 2)
    last = entries(migrated_db)[-1]
    forged = AuditRecord(occurred_at=last.occurred_at, event="test.forged")
    digest = entry_hash(3, last.entry_hash, forged)  # even with a correct hash
    with tampering(migrated_db) as conn:
        conn.execute(
            text(
                "INSERT INTO audit_entries (seq, occurred_at, event, details, prev_hash,"
                " entry_hash) VALUES (3, :at, 'test.forged', JSON_OBJECT(), :prev, :hash)"
            ),
            {"at": last.occurred_at.replace(tzinfo=None), "prev": last.entry_hash, "hash": digest},
        )
    result = _verify(migrated_db)
    assert (result.ok, result.problem, result.entries_checked) == (False, "head_mismatch", 3)


def test_a_missing_head_is_reported(migrated_db: str) -> None:
    _write(migrated_db, 1)
    with tampering(migrated_db) as conn:
        conn.execute(text("DELETE FROM audit_chain"))
    assert _verify(migrated_db).problem == "head_missing"
    with tampering(migrated_db) as conn:  # put it back for the fixture's sake
        last = conn.execute(text("SELECT seq, entry_hash FROM audit_entries")).one()
        conn.execute(
            text("INSERT INTO audit_chain (id, last_seq, last_hash) VALUES (1, :s, :h)"),
            {"s": last[0], "h": last[1]},
        )


def _rewrite_from(conn: Any, start: int, new_event: str) -> None:
    """The careful attacker: change an entry, recompute every later hash, and move the head."""
    rows = conn.execute(text("SELECT * FROM audit_entries ORDER BY seq")).mappings().all()
    prev: str = GENESIS_HASH
    for row in rows:
        if row["seq"] < start:
            prev = row["entry_hash"]
            continue
        details = row["details"] if isinstance(row["details"], dict) else json.loads(row["details"])
        record = AuditRecord(
            occurred_at=row["occurred_at"].replace(tzinfo=UTC),
            event=new_event if row["seq"] == start else row["event"],
            actor_user_id=row["actor_user_id"],
            actor_role=row["actor_role"],
            ip=row["ip"],
            request_id=row["request_id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            details=details,
        )
        digest = entry_hash(row["seq"], prev, record)
        conn.execute(
            text(
                "UPDATE audit_entries SET event = :e, prev_hash = :p, entry_hash = :h WHERE seq = :s"
            ),
            {"e": record.event, "p": prev, "h": digest, "s": row["seq"]},
        )
        prev = digest
    conn.execute(text("UPDATE audit_chain SET last_hash = :h WHERE id = 1"), {"h": prev})


def test_a_full_rewrite_is_caught_by_an_earlier_checks_record(migrated_db: str) -> None:
    _write(migrated_db, 4)
    assert _check(migrated_db).ok  # anchors entry 5 (4 events plus the check's own entry)
    with tampering(migrated_db) as conn:
        _rewrite_from(conn, 2, "test.innocent")
    # Every hash is consistent again, so the chain alone cannot tell...
    assert _verify(migrated_db).ok
    # ...but the hash recorded by the earlier check no longer matches.
    check = _check(migrated_db)
    assert (check.ok, check.problem) == (False, "anchor_mismatch")


def test_cutting_off_the_newest_entries_is_caught_by_an_earlier_checks_record(
    migrated_db: str,
) -> None:
    _write(migrated_db, 4)
    assert _check(migrated_db).ok
    with tampering(migrated_db) as conn:
        kept = conn.execute(text("SELECT entry_hash FROM audit_entries WHERE seq = 2")).scalar()
        conn.execute(text("DELETE FROM audit_entries WHERE seq > 2"))
        conn.execute(
            text("UPDATE audit_chain SET last_seq = 2, last_hash = :h WHERE id = 1"), {"h": kept}
        )
    assert _verify(migrated_db).ok  # consistent on its own
    check = _check(migrated_db)
    assert (check.ok, check.problem, check.first_bad_seq) == (False, "entries_missing", 3)


def test_checks_cannot_be_edited_or_removed(migrated_db: str) -> None:
    _check(migrated_db)
    engine = create_engine(migrated_db)
    try:
        for sql in ("UPDATE audit_checks SET ok = 0", "DELETE FROM audit_checks"):
            with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
                conn.execute(text(sql))
    finally:
        engine.dispose()


def test_a_check_sees_one_consistent_moment_while_writers_continue(migrated_db: str) -> None:
    _write(migrated_db, 50)
    # Entries written during a check are simply not part of it; nothing is reported broken.
    stop = threading.Event()

    def writer() -> None:
        while not stop.is_set():
            _write(migrated_db, 1)

    thread = threading.Thread(target=writer)
    thread.start()
    try:
        results = [_check(migrated_db) for _ in range(5)]
    finally:
        stop.set()
        thread.join()
    assert all(r.ok for r in results), [r.problem for r in results]
    assert _check(migrated_db).ok


# --- The daily job ---------------------------------------------------------------------------


@pytest.fixture
def cli_env(migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("LL_DATABASE_URL", migrated_db)
    monkeypatch.setenv("LL_SECRET_KEY", GOOD_SECRET)
    get_settings.cache_clear()
    yield migrated_db
    get_settings.cache_clear()


def test_the_daily_job_reports_and_records_its_result(
    cli_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _write(cli_env, 3)
    assert main(["verify-audit"]) == 0
    assert "Audit log intact: 3 entries checked." in capsys.readouterr().out
    with tampering(cli_env) as conn:
        conn.execute(text("UPDATE audit_entries SET event = 'test.other' WHERE seq = 2"))
    assert main(["verify-audit"]) == 1
    assert "AUDIT LOG CHECK FAILED at entry 2" in capsys.readouterr().err
    with db_session(cli_env) as db:
        triggers = [c.trigger for c in db.scalars(select(AuditCheck).order_by(AuditCheck.id))]
    assert triggers == [CheckTrigger.SCHEDULED, CheckTrigger.SCHEDULED]
