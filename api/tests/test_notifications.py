"""Chunk C19: internal alerts and escalation (BRD FR-IN-01 to FR-IN-03)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from urllib.parse import quote

import pytest
from app.alerts.job import run
from app.db import make_engine, make_session_factory
from app.files.documents import scan_pending
from app.models import Cycle, Notification, NotificationKind, Role
from app.notify.service import documents_arrived
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import events
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_alerts import at, first_cycle
from tests.test_checklist import CHECKLIST, open_portal, portal_upload, publish_with_checklist
from tests.test_clients import _ok, admin, client_body, create
from tests.test_documents import PDF, FakeScanner, scanner

__all__ = ["admin", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


@pytest.fixture
def factory(migrated_db: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(migrated_db)
    yield make_session_factory(engine)
    engine.dispose()


def setup(
    web: TestClient, admin: Signed, url: str, owner_id: int | None = None
) -> tuple[int, Cycle, str]:
    pid, _ = publish_with_checklist(web, admin, CHECKLIST)
    cid = _ok(create(web, admin, client_body(pid, owner_id=owner_id or admin.user_id)), 201)[
        "client_id"
    ]
    cycle = first_cycle(url)
    with db_session(url) as db:
        number = db.scalar(text("SELECT number FROM tickets WHERE id = :i"), {"i": cycle.ticket_id})
    return cid, cycle, f"TKT-{number:06d}"


def notes_of(url: str, cycle_id: int) -> list[Notification]:
    with db_session(url) as db:
        rows = list(
            db.scalars(
                select(Notification)
                .where(Notification.cycle_id == cycle_id)
                .order_by(Notification.id)
            )
        )
        for r in rows:
            db.expunge(r)
        return rows


def stage_of(url: str, cycle_id: int) -> str:
    with db_session(url) as db:
        return str(db.scalar(text("SELECT stage FROM cycles WHERE id = :i"), {"i": cycle_id}))


# --- FR-IN-01: the last required document moves the cycle on and tells the owner ---------------


def test_the_last_required_document_moves_the_cycle_and_notifies_the_owner(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    _, cycle, code = setup(web, admin, migrated_db)
    client, csrf = open_portal(web, admin, code)
    items = _ok(client.get("/api/portal/checklist"))["items"]
    _ok(portal_upload(client, csrf, items[0]["id"], "sales.pdf", PDF), 201)
    assert stage_of(migrated_db, cycle.id) == "scheduled"
    assert notes_of(migrated_db, cycle.id) == []
    _ok(
        portal_upload(client, csrf, items[1]["id"], "purchases.pdf", PDF), 201
    )  # the optional one waits

    assert stage_of(migrated_db, cycle.id) == "documents_received"
    notes = notes_of(migrated_db, cycle.id)
    assert [(n.kind, n.user_id) for n in notes] == [
        (NotificationKind.DOCUMENTS_RECEIVED, admin.user_id)
    ]
    listed = _ok(web.get("/api/notifications"))
    assert listed["unread"] == 1
    assert listed["items"][0]["ticket"] == code
    assert listed["items"][0]["title"].startswith("Documents received:")
    history = [h["to_stage"] for h in _ok(web.get(f"/api/tickets/{code}"))["history"]]
    assert history[:2] == ["documents_received", "awaiting_documents"]  # one step at a time
    assert len(_ok(web.get(f"/api/tickets/{code}/updates"))) == 2  # each move drafted (CR-03)
    # A later upload does not notify again.
    _ok(portal_upload(client, csrf, items[2]["id"], "notes.pdf", PDF), 201)
    assert len(notes_of(migrated_db, cycle.id)) == 1


def test_a_file_held_for_scanning_counts_once_it_is_clean(
    web: TestClient,
    admin: Signed,
    migrated_db: str,
    scanner: FakeScanner,
    factory: sessionmaker[Session],
) -> None:
    _, cycle, code = setup(web, admin, migrated_db)
    items = _ok(web.get(f"/api/tickets/{code}/checklist"))
    scanner.down = True
    for item in items[:2]:
        _ok(
            web.post(
                f"/api/uploads/tickets/{code}",
                params={"item_id": item["id"]},
                content=PDF,
                headers={**admin.headers, "X-File-Name": quote("a.pdf")},
            ),
            201,
        )
    assert stage_of(migrated_db, cycle.id) == "scheduled"
    scanner.down = False
    scan_pending(
        factory,
        web.app.state.store,  # type: ignore[attr-defined]
        scanner,
        on_clean=lambda db, cycle_id: documents_arrived(db, cycle_id, firm_name="Firm"),
    )
    assert stage_of(migrated_db, cycle.id) == "documents_received"
    assert len(notes_of(migrated_db, cycle.id)) == 1


# --- FR-IN-02: waiting on the client, with exactly what is missing ----------------------------


def test_missing_documents_after_the_grace_period_put_the_cycle_waiting_on_the_client(
    web: TestClient,
    admin: Signed,
    migrated_db: str,
    scanner: FakeScanner,
    factory: sessionmaker[Session],
) -> None:
    _, cycle, code = setup(web, admin, migrated_db)
    settings = web.app.state.settings  # type: ignore[attr-defined]
    run(factory, settings, now=at(cycle.alert_date))  # the first alert
    _ok(
        web.post(
            f"/api/uploads/tickets/{code}",
            params={"item_id": _ok(web.get(f"/api/tickets/{code}/checklist"))[0]["id"]},
            content=PDF,
            headers={**admin.headers, "X-File-Name": "sales.pdf"},
        ),
        201,
    )
    run(factory, settings, now=at(cycle.alert_date + timedelta(days=2)))
    assert [n.kind for n in notes_of(migrated_db, cycle.id)] == []
    run(
        factory, settings, now=at(cycle.alert_date + timedelta(days=3), 21)
    )  # outside sending hours too
    run(factory, settings, now=at(cycle.alert_date + timedelta(days=3), 22))
    waiting = [
        n for n in notes_of(migrated_db, cycle.id) if n.kind is NotificationKind.WAITING_ON_CLIENT
    ]
    assert len(waiting) == 1
    assert waiting[0].user_id == admin.user_id
    # Exactly the items not yet received, as on the checklist.
    assert (
        waiting[0].body.split("Still missing:\n", 1)[1]
        == "- Purchase register\n- Notes for us (optional)"
    )


# --- Done when: escalation is created once and cannot be dismissed without a note --------------


def test_an_empty_cycle_is_escalated_once_and_needs_a_note_to_dismiss(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    owner_id = make_user(migrated_db, "owner@firm.in", Role.STAFF, name="Owner Person")
    _, cycle, _ = setup(web, admin, migrated_db, owner_id=owner_id)
    settings = web.app.state.settings  # type: ignore[attr-defined]
    point = cycle.due_date - timedelta(days=2)
    run(factory, settings, now=at(point - timedelta(days=1)))
    assert [
        n for n in notes_of(migrated_db, cycle.id) if n.kind is NotificationKind.ESCALATION
    ] == []
    for hour in (10, 11, 22):
        run(factory, settings, now=at(point, hour))
    run(factory, settings, now=at(point + timedelta(days=1)))
    escalations = [
        n for n in notes_of(migrated_db, cycle.id) if n.kind is NotificationKind.ESCALATION
    ]
    assert len(escalations) == 1
    escalation = escalations[0]
    assert (escalation.for_role, escalation.user_id) == (Role.ADMINISTRATOR, None)
    assert "cycle.escalated" in events(migrated_db)

    # The Administrator sees it; the owner (Staff) does not.
    assert _ok(web.get("/api/notifications/count"))["open_escalations"] >= 1
    owner = TestClient(web.app)
    signed = sign_in_directly(migrated_db, owner, owner_id)
    assert escalation.id not in [n["id"] for n in _ok(owner.get("/api/notifications"))["items"]]
    assert (
        owner.post(
            f"/api/notifications/{escalation.id}/dismiss",
            json={"note": "x" * 10},
            headers=signed.headers,
        ).status_code
        == 404
    )

    url = f"/api/notifications/{escalation.id}/dismiss"
    for note in (None, "", "  ", "ok"):
        refused = web.post(url, json={"note": note}, headers=admin.headers)
        assert refused.status_code == 422, note
        assert refused.json()["error"]["code"] == "note_required"
    done = _ok(
        web.post(
            url,
            json={"note": "Called the client; documents promised Friday."},
            headers=admin.headers,
        )
    )
    assert (done["dismissed_by"], done["dismiss_note"]) == (
        "Test Person",
        "Called the client; documents promised Friday.",
    )
    assert (
        web.post(url, json={"note": "again"}, headers=admin.headers).json()["error"]["code"]
        == "already_dismissed"
    )
    assert "escalation.dismissed" in events(migrated_db)
    # Running again does not bring it back.
    run(factory, settings, now=at(point + timedelta(days=2)))
    assert (
        len([n for n in notes_of(migrated_db, cycle.id) if n.kind is NotificationKind.ESCALATION])
        == 1
    )


def test_the_database_refuses_a_note_less_dismissal_or_a_rewrite(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, cycle, _ = setup(web, admin, migrated_db)
    run(factory, web.app.state.settings, now=at(cycle.due_date))  # type: ignore[attr-defined]
    engine = create_engine(migrated_db)
    try:
        for sql, message in (
            (
                "UPDATE notifications SET dismissed_at = UTC_TIMESTAMP(6), dismiss_note = ' ' "
                "WHERE kind = 'escalation'",
                "needs a note",
            ),
            ("UPDATE notifications SET body = 'changed'", "cannot be changed"),
            ("DELETE FROM notifications", "never deleted"),
        ):
            with pytest.raises(Exception, match=message), engine.begin() as conn:
                conn.execute(text(sql))
    finally:
        engine.dispose()


def test_cycles_with_something_received_are_not_escalated(
    web: TestClient,
    admin: Signed,
    migrated_db: str,
    scanner: FakeScanner,
    factory: sessionmaker[Session],
) -> None:
    _, cycle, code = setup(web, admin, migrated_db)
    item = _ok(web.get(f"/api/tickets/{code}/checklist"))[0]["id"]
    _ok(
        web.post(
            f"/api/uploads/tickets/{code}",
            params={"item_id": item},
            content=PDF,
            headers={**admin.headers, "X-File-Name": "a.pdf"},
        ),
        201,
    )
    run(factory, web.app.state.settings, now=at(cycle.due_date))  # type: ignore[attr-defined]
    assert [n.kind for n in notes_of(migrated_db, cycle.id)] == []
    read = _ok(web.get("/api/notifications"))
    assert all(n["kind"] != "escalation" or n["ticket"] != code for n in read["items"])
