"""Chunk C03: the audit trail is complete, append-only and hash-chained (BRD FR-AU-01 to 03)."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from typing import Any

import pytest
from app.audit import AuditChainMissing, append, audit, make_record, write_now
from app.audit.writer import MAX_DETAIL_TEXT, clean_details
from app.auth.events import security_event
from app.db import make_engine, make_session_factory
from app.models import GENESIS_HASH, AppMeta, AuditEntry, Role
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.audit_helpers import assert_chain_intact, entries, events
from tests.helpers import PASSWORD, db_session, login, make_user, sign_in

APP_DB_URL = os.environ.get("LL_TEST_APP_DATABASE_URL")


# --- Done when: a scripted set of actions produces the expected entries with no gaps ---------


@pytest.mark.requires_db
def test_a_scripted_session_produces_the_expected_trail(web: TestClient, migrated_db: str) -> None:
    boss = make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    clerk = make_user(migrated_db, "clerk@firm.in", Role.STAFF)

    assert login(web, "boss@firm.in", "not the password").status_code == 401
    admin = sign_in(web, "boss@firm.in")
    created = web.post(
        "/api/admin/users",
        json={"email": "new@firm.in", "full_name": "New Person", "role": "staff"},
        headers=admin.headers,
    )
    assert created.status_code == 201, created.text
    new_id = created.json()["person"]["id"]
    assert web.post("/api/admin/users/1/enable").status_code == 403  # no CSRF token
    foreign = {**admin.headers, "Origin": "https://evil.example"}
    assert web.post("/api/admin/users/1/enable", headers=foreign).status_code == 403
    assert web.post("/api/auth/logout", headers=admin.headers).status_code == 204
    assert web.get("/api/auth/me").status_code == 401  # signed out: routine, not recorded
    sign_in(web, "clerk@firm.in")
    assert web.get("/api/admin/users").status_code == 403

    rows = entries(migrated_db)
    assert [r.event for r in rows] == [
        "login.failed",
        "login.password_ok",
        "login.succeeded",
        "user.created",
        "csrf.rejected",
        "origin.rejected",
        "logout",
        "login.password_ok",
        "login.succeeded",
        "access.denied",
    ]
    assert_chain_intact(rows)
    by_event: dict[str, AuditEntry] = {}
    for row in rows:
        by_event.setdefault(row.event, row)  # the first of each
    assert by_event["login.failed"].actor_user_id == boss
    assert by_event["login.succeeded"].actor_role == "administrator"
    created_row = by_event["user.created"]
    assert (created_row.actor_user_id, created_row.target_type, created_row.target_id) == (
        boss,
        "user",
        str(new_id),
    )
    assert created_row.details == {"role": "staff"}
    denied = by_event["access.denied"]
    assert (denied.actor_user_id, denied.actor_role) == (clerk, "staff")
    assert denied.details["route"] == "GET /api/admin/users"
    assert by_event["origin.rejected"].details == {
        "method": "POST",
        "route": "unmatched",
        "status": 403,
    }
    assert all(r.request_id and r.ip == "testclient" for r in rows)
    assert len({r.request_id for r in rows if r.event == "login.succeeded"}) == 2


@pytest.mark.requires_db
def test_refusals_without_their_own_entry_are_still_recorded(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "temp@firm.in", Role.STAFF, must_change=True)
    signed = sign_in(web, "temp@firm.in")
    before = len(entries(migrated_db))
    # Allowed to sign in, not yet allowed to work: a 403 that no code audits by itself.
    assert web.get("/api/admin/users").status_code == 403
    web.cookies.set("ll_session", "a-made-up-session-token")
    assert web.get("/api/auth/me").status_code == 401  # a cookie that is not a session
    rows = entries(migrated_db)[before:]
    assert [(r.event, r.details["status"]) for r in rows] == [
        ("request.denied", 403),
        ("request.denied", 401),
    ]
    assert rows[0].details["route"] == "/api/admin/users"
    assert signed.user_id  # the first refusal happened inside a real session
    assert_chain_intact(entries(migrated_db))


def test_a_broken_audit_database_does_not_turn_a_refusal_into_an_error(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    # `client` points at a database that does not exist.
    with caplog.at_level(logging.ERROR, logger="ledgerline.security"):
        resp = client.post("/api/auth/logout", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert any(r.getMessage() == "audit entry not written" for r in caplog.records)


def test_outside_a_request_an_entry_without_a_session_is_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="ledgerline.security"):
        security_event("access.denied", user_id=1)
    assert any("no database" in r.getMessage() for r in caplog.records)


# --- Writing: same transaction, own transaction, concurrency ---------------------------------


@pytest.mark.requires_db
def test_an_entry_is_saved_with_its_change_or_not_at_all(migrated_db: str) -> None:
    k1, k2 = f"t-{uuid.uuid4().hex[:12]}", f"t-{uuid.uuid4().hex[:12]}"  # app_meta is kept
    with db_session(migrated_db) as db:
        db.add(AppMeta(key=k1, value="v"))
        audit(db, "test.kept", target=("meta", k1))
        db.commit()

        db.add(AppMeta(key=k2, value="v"))
        audit(db, "test.rolled_back")
        db.rollback()

        audit(db, "test.closed_without_commit")
        db.close()
        db.commit()  # the same session, used again: the closed transaction's entry is gone
    with db_session(migrated_db) as db:
        db.add(AppMeta(key=k1, value="duplicate"))  # the primary key clash fails the commit
        audit(db, "test.failed_commit")
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        db.commit()  # an empty commit must not pick up the abandoned entry
    assert events(migrated_db) == ["test.kept"]
    assert entries(migrated_db)[0].target_id == k1


@pytest.mark.requires_db
def test_several_entries_in_one_commit_are_consecutive(migrated_db: str) -> None:
    with db_session(migrated_db) as db:
        for n in range(5):
            audit(db, "test.batch", details={"n": n})
        db.commit()
    rows = entries(migrated_db)
    assert [r.details["n"] for r in rows] == [0, 1, 2, 3, 4]
    assert_chain_intact(rows)


@pytest.mark.requires_db
def test_concurrent_writers_never_fork_or_skip(migrated_db: str) -> None:
    engine = make_engine(migrated_db, pool_size=8)
    factory = make_session_factory(engine)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(10):
                if i % 2:
                    write_now(factory, [make_record("test.own", details={"w": n, "i": i})])
                else:
                    with factory() as db:
                        audit(db, "test.shared", details={"w": n, "i": i})
                        db.commit()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    engine.dispose()
    assert errors == []
    rows = entries(migrated_db)
    assert len(rows) == 80
    assert_chain_intact(rows)


@pytest.mark.requires_db
def test_a_missing_chain_head_stops_the_work_instead_of_starting_a_new_chain(
    migrated_db: str,
) -> None:
    lost = f"t-{uuid.uuid4().hex[:12]}"
    engine = create_engine(migrated_db)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE audit_chain"))  # needs the owner account
    engine.dispose()
    with db_session(migrated_db) as db:
        db.add(AppMeta(key=lost, value="v"))
        audit(db, "test.orphan")
        with pytest.raises(AuditChainMissing):
            db.commit()
    with db_session(migrated_db) as db:
        assert db.get(AppMeta, lost) is None


# --- The database refuses tampering (LL-015) -------------------------------------------------


def _seed_two(url: str) -> None:
    with db_session(url) as db:
        audit(db, "test.one")
        audit(db, "test.two")
        db.commit()


def _refused(url: str, sql: str, **params: Any) -> str:
    engine = create_engine(url)
    try:
        with pytest.raises(DBAPIError) as info, engine.begin() as conn:
            conn.execute(text(sql), params)
    finally:
        engine.dispose()
    return str(info.value.orig)


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_entries SET event = 'changed' WHERE seq = 1",
        "UPDATE audit_entries SET details = JSON_OBJECT()",
        "DELETE FROM audit_entries WHERE seq = 2",
        "DELETE FROM audit_entries",
    ],
)
def test_entries_cannot_be_changed_or_removed(migrated_db: str, sql: str) -> None:
    _seed_two(migrated_db)
    assert "append-only" in _refused(migrated_db, sql)
    assert events(migrated_db) == ["test.one", "test.two"]


@pytest.mark.requires_db
def test_an_insert_must_extend_the_chain(migrated_db: str) -> None:
    _seed_two(migrated_db)
    head = entries(migrated_db)[-1]
    insert = (
        "INSERT INTO audit_entries (seq, occurred_at, event, details, prev_hash, entry_hash) "
        "VALUES (:seq, UTC_TIMESTAMP(6), 'forged', JSON_OBJECT(), :prev, :hash)"
    )
    fake = "f" * 64
    # A gap, a fork from an older entry, and a re-used number are all refused.
    for seq, prev in ((4, head.entry_hash), (3, head.prev_hash), (2, head.prev_hash)):
        message = _refused(migrated_db, insert, seq=seq, prev=prev, hash=fake)
        assert "does not extend the chain" in message or "Duplicate" in message
    assert events(migrated_db) == ["test.one", "test.two"]


@pytest.mark.requires_db
@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE audit_chain SET last_seq = 0, last_hash = :genesis",
        "UPDATE audit_chain SET last_seq = last_seq + 5",
        "DELETE FROM audit_chain",
    ],
)
def test_the_chain_head_only_moves_forward(migrated_db: str, sql: str) -> None:
    _seed_two(migrated_db)
    message = _refused(migrated_db, sql, genesis=GENESIS_HASH)
    assert "audit_chain" in message


@pytest.mark.requires_db
def test_the_app_account_cannot_remove_the_protections(migrated_db: str) -> None:
    """The running app's own database account: may add entries, may not undo the safeguards."""
    if not APP_DB_URL:
        pytest.skip("LL_TEST_APP_DATABASE_URL (the restricted app account) not set")
    _seed_two(migrated_db)
    factory = make_session_factory(make_engine(APP_DB_URL))
    write_now(factory, [make_record("test.by_app")])
    for sql in (
        "TRUNCATE TABLE audit_entries",
        "DROP TRIGGER audit_entries_no_delete",
        "DROP TABLE audit_entries",
        "ALTER TABLE audit_entries ADD COLUMN x INT",
        "CREATE TRIGGER sneaky BEFORE INSERT ON audit_entries FOR EACH ROW SET NEW.event = 'x'",
    ):
        message = _refused(APP_DB_URL, sql)
        assert "denied" in message, (sql, message)
    for sql in ("UPDATE audit_entries SET event = 'x'", "DELETE FROM audit_entries"):
        assert "append-only" in _refused(APP_DB_URL, sql)
    assert events(migrated_db) == ["test.one", "test.two", "test.by_app"]
    assert_chain_intact(entries(migrated_db))


# --- What goes into an entry -----------------------------------------------------------------


def test_details_never_hold_identifiers_and_stay_small() -> None:
    cleaned = clean_details(
        {
            "note": "PAN ABCDE1234F, mail a.b@firm.in",
            "ratio": 0.5,
            "huge": 2**70,
            "role": Role.STAFF,
            "ids": {3, 1, 2},
            "long": "x" * 1000,
            "flag": True,
            "none": None,
        }
    )
    assert "ABCDE1234F" not in str(cleaned)
    assert "a.b@firm.in" not in str(cleaned)
    assert cleaned["ratio"] == "0.5"  # floats would not hash the same after a MySQL round trip
    assert cleaned["huge"] == str(2**70)
    assert cleaned["role"] == "staff"
    assert cleaned["ids"] == [1, 2, 3]
    assert len(cleaned["long"]) == MAX_DETAIL_TEXT
    assert (cleaned["flag"], cleaned["none"]) == (True, None)
    assert clean_details({f"k{i}": "y" * 250 for i in range(30)}) == {"truncated": True}


def test_event_names_are_bounded() -> None:
    with pytest.raises(ValueError, match="1 to 64"):
        make_record("")
    with pytest.raises(ValueError, match="1 to 64"):
        make_record("e" * 65)


@pytest.mark.requires_db
def test_unicode_and_microseconds_survive_the_round_trip(migrated_db: str) -> None:
    with db_session(migrated_db) as db:
        audit(db, "test.unicode", details={"name": "Śrī Gaṇeśa ₹ 🧾"})
        db.commit()
    rows = entries(migrated_db)
    assert rows[0].details == {"name": "Śrī Gaṇeśa ₹ 🧾"}
    assert_chain_intact(rows)


@pytest.mark.requires_db
def test_append_with_nothing_to_write_changes_nothing(migrated_db: str) -> None:
    with db_session(migrated_db) as db:
        assert append(db, []) == 0
        db.commit()
    assert entries(migrated_db) == []


@pytest.mark.requires_db
def test_sign_in_failures_are_kept_even_though_the_request_fails(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "who@firm.in", Role.STAFF)
    for _ in range(5):
        login(web, "who@firm.in", "wrong password!")
    assert events(migrated_db) == ["login.failed"] * 4 + ["login.failed", "login.locked"]
    # Now locked: even the right password is refused, and that is recorded too.
    assert login(web, "who@firm.in", PASSWORD).status_code == 401
    assert events(migrated_db)[-1] == "login.failed"
