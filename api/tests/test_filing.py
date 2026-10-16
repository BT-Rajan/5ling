"""Chunk C23: filing and closure (BRD FR-WF-05, FR-WF-02, FR-CY-02, CR-03)."""

from __future__ import annotations

from itertools import pairwise
from typing import Any

import pytest
from app.cycles.stages import ORDER
from app.models import (
    Cycle,
    CycleFiling,
    Email,
    EmailKind,
    EmailStatus,
    EventKind,
    Role,
    StateEvent,
    Ticket,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.audit_helpers import events
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_assignments import assign, consultant, setup, to_documents_received
from tests.test_clients import _ok, admin
from tests.test_documents import PDF, FakeScanner, scanner
from tests.test_tickets import checker, move

__all__ = ["admin", "checker", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


def ready_to_file(
    web: TestClient, admin: Signed, checker: tuple[TestClient, Signed], code: str
) -> None:
    to_documents_received(web, admin, code)
    for action, at in (
        ("assign", "documents_received"),
        ("start_preparing", "assigned"),
        ("submit_for_review", "preparing"),
        ("approve", "in_review"),
    ):
        _ok(move(*(checker if action == "approve" else (web, admin)), code, action, at))


def file_it(web: TestClient, signed: Signed, code: str, reference: str, data: bytes = PDF) -> Any:
    return web.post(
        f"/api/uploads/tickets/{code}/filing",
        # Ignored unless the cycle is past its due date, which depends on the day the tests run.
        params={"reference": reference, "late_reason": "Papers came in late"},
        content=data,
        headers={**signed.headers, "X-File-Name": "ack.pdf"},
    )


def cycle_of(url: str, code: str) -> Cycle:
    with db_session(url) as db:
        number = int(code.removeprefix("TKT-"))
        cycle = db.scalar(
            select(Cycle).join(Ticket, Ticket.id == Cycle.ticket_id).where(Ticket.number == number)
        )
        assert cycle is not None
        db.expunge(cycle)
        return cycle


# --- LL-096: Filed needs the uploaded acknowledgement and the reference number -------------------


def test_filed_is_blocked_until_both_the_acknowledgement_and_reference_are_given(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: FakeScanner,
) -> None:
    _, code, _ = setup(web, admin, migrated_db)

    # Too early: only work that is ready to file.
    early = file_it(web, admin, code, "AA0705260012345")
    assert (early.status_code, early.json()["error"]["code"]) == (409, "illegal_transition")

    ready_to_file(web, admin, checker, code)
    ticket = _ok(web.get(f"/api/tickets/{code}"))["cycle"]
    assert ticket["can_file"] is True
    assert "file" not in [m["action"] for m in ticket["moves"]]

    # The plain move is refused: there is no proof.
    bare = web.post(
        f"/api/tickets/{code}/transition",
        json={"action": "file", "expected_stage": "ready_to_file"},
        headers=admin.headers,
    )
    assert (bare.status_code, bare.json()["error"]["code"]) == (409, "proof_required")

    # No reference, or not one: refused, nothing stored.
    for bad in ("", "  ", "AB", "ARN 1234", "<script>", "-12345", "A" * 51):
        refused = file_it(web, admin, code, bad)
        assert refused.status_code == 422, (bad, refused.text)
    # No file: refused.
    empty = file_it(web, admin, code, "AA0705260012345", data=b"")
    assert (empty.status_code, empty.json()["error"]["code"]) == (422, "file_refused")
    # A virus: refused.
    infected = file_it(web, admin, code, "AA0705260012345", data=PDF + EICAR)
    assert (infected.status_code, infected.json()["error"]["code"]) == (422, "infected")
    assert _ok(web.get(f"/api/tickets/{code}/documents")) == []
    assert _ok(web.get(f"/api/tickets/{code}"))["cycle"]["stage"] == "ready_to_file"

    # Both: filed, with the proof shown.
    filed = _ok(file_it(web, admin, code, " aa0705260012345 "))
    assert filed["cycle"]["stage"] == "filed"
    assert filed["cycle"]["can_file"] is False
    proof = filed["cycle"]["proof"]
    assert (proof["reference"], proof["filename"], proof["filed_by"]) == (
        "AA0705260012345",
        "ack.pdf",
        "Test Person",
    )
    docs = _ok(web.get(f"/api/tickets/{code}/documents"))
    assert [(d["title"], d["versions"][0]["id"]) for d in docs] == [
        ("Filing acknowledgement", proof["version_id"])
    ]
    assert "cycle.filed" in events(migrated_db)

    # Once only.
    again = file_it(web, admin, code, "AA0705260099999")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "illegal_transition")


def test_the_database_refuses_filed_without_proof_and_keeps_proof_unchanged(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: FakeScanner,
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    ready_to_file(web, admin, checker, code)
    cycle = cycle_of(migrated_db, code)
    with db_session(migrated_db) as db, pytest.raises(DBAPIError, match="without proof"):
        db.execute(text("UPDATE cycles SET stage = 'filed' WHERE id = :id"), {"id": cycle.id})
    _ok(file_it(web, admin, code, "AA0705260012345"))
    for sql, message in (
        ("UPDATE cycle_filings SET reference = 'X1234'", "never changed"),
        ("DELETE FROM cycle_filings", "never deleted"),
    ):
        with db_session(migrated_db) as db, pytest.raises(DBAPIError, match=message):
            db.execute(text(sql))
    with db_session(migrated_db) as db:
        assert db.scalar(select(CycleFiling.reference)) == "AA0705260012345"


def test_only_the_owner_or_an_administrator_records_the_filing(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: FakeScanner,
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF, name="Sita Staff")
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    _ok(assign(web, admin, code, sid))
    _ok(move(sita, sita_s, code, "start_preparing", "assigned"))
    _ok(move(sita, sita_s, code, "submit_for_review", "preparing"))
    _ok(move(web, admin, code, "approve", "in_review"))
    # The in-house assignee sees the ticket but may not record the filing.
    assert _ok(sita.get(f"/api/tickets/{code}"))["cycle"]["can_file"] is False
    refused = file_it(sita, sita_s, code, "AA0705260012345")
    assert (refused.status_code, refused.json()["error"]["code"]) == (403, "forbidden")
    assert _ok(file_it(web, admin, code, "AA0705260012345"))["cycle"]["stage"] == "filed"


# --- LL-097: closing ends outside access; the next cycle is already scheduled --------------------


def test_closing_ends_the_consultants_access_and_the_next_cycle_is_already_scheduled(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    cid, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    uid, ravi, ravi_s = consultant(web, migrated_db)
    _ok(assign(web, admin, code, uid))
    work_id = _ok(ravi.get("/api/consultant/assignments"))[0]["id"]
    _ok(
        ravi.post(
            f"/api/uploads/consultant/assignments/{work_id}",
            content=PDF + b"%work",
            headers={**ravi_s.headers, "X-File-Name": "computation.pdf"},
        ),
        201,
    )
    _ok(ravi.post(f"/api/consultant/assignments/{work_id}/submit", json={}, headers=ravi_s.headers))
    _ok(move(web, admin, code, "approve", "in_review"))
    _ok(file_it(web, admin, code, "AA0705260012345"))
    _ok(move(web, admin, code, "acknowledge", "filed"))
    assert ravi.get(f"/api/consultant/assignments/{work_id}").status_code == 200

    _ok(move(web, admin, code, "close", "acknowledged"))
    closed = ravi.get(f"/api/consultant/assignments/{work_id}")
    assert (closed.status_code, closed.json()["error"]["code"]) == (403, "no_access")
    assert _ok(ravi.get("/api/consultant/assignments")) == []

    # The next period of the same filing is already there, waiting.
    done = cycle_of(migrated_db, code)
    with db_session(migrated_db) as db:
        later = db.scalars(
            select(Cycle)
            .where(
                Cycle.client_pk == done.client_pk,
                Cycle.filing_id == done.filing_id,
                Cycle.period_start > done.period_start,
            )
            .order_by(Cycle.period_start)
        ).first()
        assert later is not None
        assert later.stage.value == "scheduled"
    assert cid


# --- LL-098: the whole lifecycle, with a draft at every state change ----------------------------


def test_scheduled_to_closed_with_a_client_draft_at_every_state_change(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: FakeScanner,
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
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
    assert [h["to_stage"] for h in reversed(final["history"])] == [s.value for s in ORDER]
    assert final["cycle"]["proof"]["reference"] == "AA0705260012345"

    cycle = cycle_of(migrated_db, code)
    with db_session(migrated_db) as db:
        stage_events = list(
            db.scalars(
                select(StateEvent)
                .where(
                    StateEvent.ticket_id == cycle.ticket_id,
                    StateEvent.kind == EventKind.CYCLE_STAGE,
                )
                .order_by(StateEvent.id)
            )
        )
        assert [(e.from_state, e.to_state) for e in stage_events] == [
            (s.value, t.value) for s, t in pairwise(ORDER)
        ]
        assert all(e.handled_at is not None for e in stage_events)
        drafts = {
            e.state_event_id: e
            for e in db.scalars(
                select(Email).where(
                    Email.kind == EmailKind.CLIENT_UPDATE, Email.ticket_id == cycle.ticket_id
                )
            )
        }
        # CR-03: one draft per change, and none sent without a person pressing Send.
        assert set(drafts) == {e.id for e in stage_events}
        assert all(d.status is EmailStatus.DRAFT for d in drafts.values())
