"""Chunk C09: tickets and cycle stages (BRD FR-CY-02, FR-WF-04, CR-02, CR-03)."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from app.cycles.service import create_cycle, parse_ticket, ticket_code
from app.cycles.stages import MOVES, ORDER
from app.models import CalendarVersion, Client, Filing, PackageFiling, Role, Stage, StateEvent
from app.schedule.service import rule_for, schedule_year
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text

from tests.audit_helpers import events
from tests.helpers import Signed, break_glass_ticket, db_session, make_user, sign_in_directly
from tests.test_clients import _ok, admin, client_body, create, publish_package

__all__ = ["admin"]  # the fixture, re-used from test_clients


def test_ticket_ids_are_read_however_they_are_typed() -> None:
    assert ticket_code(1) == "TKT-000001"
    assert ticket_code(1234567) == "TKT-1234567"
    for typed in ("TKT-000123", "tkt-123", "TKT123", "123", " 000123 "):
        assert parse_ticket(typed) == 123
    for bad in ("TKT-", "TKT-0", "abc", "TKT-12a", "-5"):
        assert parse_ticket(bad) is None


def test_the_stage_table_never_skips_a_stage() -> None:
    assert [s.value for s in ORDER][:3] == ["scheduled", "awaiting_documents", "documents_received"]
    assert len(ORDER) == 10
    forward = [m for m in MOVES if ORDER.index(m.target) > ORDER.index(m.source)]
    skips = [m.action for m in forward if ORDER.index(m.target) - ORDER.index(m.source) != 1]
    # The one exception: a nil filing the client confirmed goes straight to review (FR-CY-03,
    # C34). Only the system makes it, and only with the stored confirmation.
    assert skips == ["no_activity"]
    # Every stage but the last can be left by moving forward.
    assert {m.source for m in forward} == set(ORDER[:-1])


# --- Through the API -------------------------------------------------------------------------


def first_ticket(url: str, client_id: int) -> str:
    """The client's earliest cycle; the generator (C10) makes them when the client is saved."""
    with db_session(url) as db:
        number = db.scalar(
            text(
                "SELECT t.number FROM cycles c JOIN tickets t ON t.id = c.ticket_id"
                " JOIN clients k ON k.id = c.client_pk WHERE k.client_id = :c"
                " ORDER BY c.period_start, t.number LIMIT 1"
            ),
            {"c": client_id},
        )
    return ticket_code(int(number))


def make_cycle(url: str, client_id: int) -> str:
    """Make the first period of 2026-27 for the client's first filing, by hand."""
    with db_session(url) as db:
        client = db.scalar(select(Client).where(Client.client_id == client_id))
        assert client is not None
        filing_id = db.scalar(
            select(PackageFiling.filing_id).where(
                PackageFiling.package_version_id == client.package_version_id
            )
        )
        filing = db.get(Filing, filing_id)
        calendar = db.scalar(
            select(CalendarVersion)
            .where(
                CalendarVersion.filing_id == filing_id, CalendarVersion.published_at.is_not(None)
            )
            .order_by(CalendarVersion.version.desc())
        )
        assert filing is not None
        assert calendar is not None
        first = schedule_year(db, rule_for(db, calendar), 2026)[0]
        cycle = create_cycle(
            db,
            client=client,
            filing=filing,
            calendar_version=calendar,
            package_version_id=client.package_version_id,
            period_key=first.period.key,
            period_label=first.period.label,
            period_start=first.period.start,
            period_end=first.period.end,
            due_date=first.due_date,
            alert_date=first.alert_date,
        )
        db.commit()
        number = db.scalar(text("SELECT number FROM tickets WHERE id = :i"), {"i": cycle.ticket_id})
    return ticket_code(int(number))


@pytest.fixture
def checker(web: TestClient, migrated_db: str) -> tuple[TestClient, Signed]:
    """A second Administrator: approving needs someone who did not prepare the work (C22)."""
    uid = make_user(migrated_db, "checker@firm.in", Role.ADMINISTRATOR, name="Checker Person")
    client = TestClient(web.app)
    return client, sign_in_directly(migrated_db, client, uid)


# A minimal PDF, as the portal's acknowledgement.
ACKNOWLEDGEMENT = b"%PDF-1.7\n1 0 obj << >> endobj\ntrailer << >>\n%%EOF\n"


def move(
    web: TestClient, signed: Signed, code: str, action: str, expected: str, note: str | None = None
) -> Any:
    if action == "assign":  # made by assigning the work (C21), here to the person moving it
        return web.post(
            f"/api/tickets/{code}/assignment",
            json={"assignee_id": signed.user_id},
            headers=signed.headers,
        )
    if action == "withdraw_assignment":
        return web.post(
            f"/api/tickets/{code}/assignment/withdraw",
            json={"note": note or "Taken back"},
            headers=signed.headers,
        )
    if action == "file" and expected == "ready_to_file":  # filed with its proof (C23)
        return web.post(
            f"/api/uploads/tickets/{code}/filing",
            params={"reference": note or "AA0705260012345", "late_reason": "Papers came in late"},
            content=ACKNOWLEDGEMENT,
            headers={**signed.headers, "X-File-Name": "acknowledgement.pdf"},
        )
    if action == "approve":  # the checker is often an Administrator who does not own the client
        break_glass_ticket(web, signed, code)
    body: dict[str, Any] = {"action": action, "expected_stage": expected}
    if note is not None:
        body["note"] = note
    return web.post(f"/api/tickets/{code}/transition", json=body, headers=signed.headers)


def state_events(url: str) -> list[tuple[str, str, str]]:
    with db_session(url) as db:
        rows = db.scalars(select(StateEvent).order_by(StateEvent.id))
        return [(e.kind.value, e.from_state, e.to_state) for e in rows]


# --- Done when: no stage can be skipped; every cycle has a TKT ID; each stage change emits a
# state_changed event -------------------------------------------------------------------------


@pytest.mark.requires_db
def test_a_cycle_walks_every_stage_in_order_and_nothing_can_be_skipped(
    web: TestClient, admin: Signed, migrated_db: str, checker: tuple[TestClient, Signed]
) -> None:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    code = first_ticket(migrated_db, cid)
    assert code == "TKT-000001"
    ticket = _ok(web.get(f"/api/tickets/{code}"))
    assert ticket["type"] == "filing"
    assert ticket["cycle"]["stage"] == "scheduled"
    assert ticket["cycle"]["period_label"] == "Apr 2026"
    assert ticket["cycle"]["due_date"] == "2026-05-11"

    # Skipping is refused from the very start, whatever is asked for.
    for action in ("documents_received", "approve", "file", "close", "made_up"):
        refused = move(web, admin, code, action, "scheduled")
        assert refused.status_code == 409, action
        assert refused.json()["error"]["code"] == "illegal_transition"
        assert refused.json()["error"]["allowed"] == ["request_documents"]

    path = [
        ("request_documents", "scheduled"),
        ("documents_received", "awaiting_documents"),
        ("assign", "documents_received"),
        ("start_preparing", "assigned"),
        ("submit_for_review", "preparing"),
        ("approve", "in_review"),
        ("file", "ready_to_file"),
        ("acknowledge", "filed"),
        ("close", "acknowledged"),
    ]
    for action, at in path:
        moved = move(*(checker if action == "approve" else (web, admin)), code, action, at)
        assert moved.status_code == 200, (action, moved.text)
    final = _ok(web.get(f"/api/tickets/{code}"))
    assert final["cycle"]["stage"] == "closed"
    assert final["cycle"]["moves"] == []
    assert [h["to_stage"] for h in reversed(final["history"])] == [s.value for s in ORDER]

    # One state event per move, in order (consumed by chunk C15).
    expected = [("cycle_stage", s.value, t.value) for s, t in pairwise(ORDER)]
    assert state_events(migrated_db) == expected
    assert events(migrated_db).count("cycle.stage_changed") == 9


@pytest.mark.requires_db
def test_review_can_return_work_with_a_note_but_not_without(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    code = first_ticket(migrated_db, cid)
    for action, at in [
        ("request_documents", "scheduled"),
        ("documents_received", "awaiting_documents"),
        ("assign", "documents_received"),
        ("start_preparing", "assigned"),
        ("submit_for_review", "preparing"),
    ]:
        _ok(move(web, admin, code, action, at))
    silent = move(web, admin, code, "return", "in_review")
    assert silent.json()["error"]["code"] == "note_required"
    back = _ok(move(web, admin, code, "return", "in_review", note="Schedule 3 totals do not match"))
    assert back["cycle"]["stage"] == "preparing"
    assert back["history"][0]["note"] == "Schedule 3 totals do not match"


@pytest.mark.requires_db
def test_a_second_click_does_not_move_twice(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    code = first_ticket(migrated_db, cid)
    _ok(move(web, admin, code, "request_documents", "scheduled"))
    again = move(web, admin, code, "request_documents", "scheduled")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "stage_changed"
    assert len(state_events(migrated_db)) == 1


@pytest.mark.requires_db
def test_client_status_changes_are_state_events_too(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"status": "paused", "reason": "Client asked"},
            headers=admin.headers,
        )
    )
    assert state_events(migrated_db) == [("client_status", "active", "paused")]


# --- Access and integrity ---------------------------------------------------------------------


@pytest.mark.requires_db
def test_only_the_owner_and_the_administrator_see_or_move_a_ticket(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    owner_id = make_user(migrated_db, "owner@firm.in", Role.STAFF)
    other_id = make_user(migrated_db, "other@firm.in", Role.STAFF)
    cid = _ok(create(web, admin, client_body(pid, owner_id=owner_id)), 201)["client_id"]
    code = first_ticket(migrated_db, cid)
    other = TestClient(web.app)
    signed_other = sign_in_directly(migrated_db, other, other_id)
    assert other.get(f"/api/tickets/{code}").status_code == 403
    assert move(other, signed_other, code, "request_documents", "scheduled").status_code == 403
    assert other.get(f"/api/clients/{cid}/tickets").status_code == 403
    owner = TestClient(web.app)
    signed_owner = sign_in_directly(migrated_db, owner, owner_id)
    assert owner.get(f"/api/tickets/{code}").status_code == 200
    assert move(owner, signed_owner, code, "request_documents", "scheduled").status_code == 200
    listed = _ok(owner.get(f"/api/clients/{cid}/tickets"))
    assert (listed[0]["code"], listed[0]["stage"]) == (code, "awaiting_documents")
    assert all(t["stage"] == "scheduled" for t in listed[1:])  # the rest of the year
    assert owner.get("/api/tickets/TKT-999999").status_code == 404
    assert owner.get("/api/tickets/not-a-ticket").status_code == 404


@pytest.mark.requires_db
def test_ticket_numbers_history_and_events_cannot_be_rewritten(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    code = first_ticket(migrated_db, cid)
    _ok(move(web, admin, code, "request_documents", "scheduled"))
    engine = create_engine(migrated_db)
    try:
        for sql, message in (
            ("UPDATE tickets SET number = 42", "cannot be changed"),
            ("DELETE FROM tickets", "never deleted"),
            ("DELETE FROM ticket_counter", "final"),
            ("UPDATE cycle_stage_changes SET note = 'x'", "append-only"),
            ("DELETE FROM cycle_stage_changes", "append-only"),
            ("UPDATE state_events SET to_state = 'closed'", "handled, once"),
            ("DELETE FROM state_events", "kept"),
        ):
            with pytest.raises(Exception, match=message), engine.begin() as conn:
                conn.execute(text(sql))
        # Marking an event handled is the one change allowed, and only once: drafting its
        # client update (C15) already did, so it cannot be marked again.
        with engine.begin() as conn:
            handled = conn.execute(
                text("SELECT COUNT(*) FROM state_events WHERE handled_at IS NULL")
            ).scalar()
        assert handled == 0
        with pytest.raises(Exception, match="handled, once"), engine.begin() as conn:
            conn.execute(text("UPDATE state_events SET handled_at = UTC_TIMESTAMP(6)"))
    finally:
        engine.dispose()


@pytest.mark.requires_db
def test_the_same_period_cannot_have_two_cycles(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    first_ticket(migrated_db, cid)  # the generator already made April 2026
    with pytest.raises(Exception, match="Duplicate entry"):
        make_cycle(migrated_db, cid)
    with db_session(migrated_db) as db:
        assert db.scalar(select(func.count()).select_from(StateEvent)) == 0


@pytest.mark.requires_db
def test_the_stage_list_is_published_for_the_screens(web: TestClient, admin: Signed) -> None:
    body = _ok(web.get("/api/stages"))
    assert body["stages"][0]["label"] == "Scheduled"
    assert len(body["stages"]) == 10
    assert any(m["action"] == "return" and m["needs_note"] for m in body["moves"])
    assert Stage.CLOSED.value == body["stages"][-1]["key"]
