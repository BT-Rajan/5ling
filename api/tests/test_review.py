"""Chunk C22: maker-checker review (BRD FR-WF-03, FR-WF-04)."""

from __future__ import annotations

import pytest
from app.models import Role
from fastapi.testclient import TestClient

from tests.audit_helpers import entries
from tests.helpers import Signed, break_glass_ticket, make_user, sign_in_directly
from tests.test_assignments import assign, consultant, setup, to_documents_received
from tests.test_clients import _ok, admin
from tests.test_documents import PDF, scanner
from tests.test_tickets import checker, move

__all__ = ["admin", "checker", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


def stage(web: TestClient, code: str) -> str:
    return str(_ok(web.get(f"/api/tickets/{code}"))["cycle"]["stage"])


def actions(web: TestClient, code: str) -> list[str]:
    return [m["action"] for m in _ok(web.get(f"/api/tickets/{code}"))["cycle"]["moves"]]


def upload(browser: TestClient, signed: Signed, work_id: int) -> object:
    return browser.post(
        f"/api/uploads/consultant/assignments/{work_id}",
        content=PDF + b"%work",
        headers={**signed.headers, "X-File-Name": "computation.pdf"},
    )


# --- LL-092, LL-093: the preparer sends work for review; the checker approves or returns it ------


def test_a_consultant_prepares_sends_for_review_and_sees_the_reviewers_comment(
    web: TestClient, admin: Signed, migrated_db: str, scanner: object
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    uid, ravi, ravi_s = consultant(web, migrated_db)
    _ok(assign(web, admin, code, uid))
    work_id = _ok(ravi.get("/api/consultant/assignments"))[0]["id"]
    detail = _ok(ravi.get(f"/api/consultant/assignments/{work_id}"))
    assert (detail["can_upload"], detail["can_submit"], detail["review"]) == (True, False, [])

    # Nothing to review yet.
    early = ravi.post(
        f"/api/consultant/assignments/{work_id}/submit", json={}, headers=ravi_s.headers
    )
    assert early.status_code == 409, early.text
    assert early.json()["error"]["code"] == "illegal_transition"

    # Their first work starts the preparing.
    assert upload(ravi, ravi_s, work_id).status_code == 201  # type: ignore[attr-defined]
    assert stage(web, code) == "preparing"
    assert _ok(ravi.get(f"/api/consultant/assignments/{work_id}"))["can_submit"] is True

    sent = _ok(
        ravi.post(
            f"/api/consultant/assignments/{work_id}/submit",
            json={"note": "Computation attached"},
            headers=ravi_s.headers,
        )
    )
    assert stage(web, code) == "in_review"
    assert (sent["can_upload"], sent["can_submit"]) == (False, False)
    assert [(r["action"], r["note"], r["by"]) for r in sent["review"]] == [
        ("submit_for_review", "Computation attached", "Ravi Consultant")
    ]

    # What is under review cannot be swapped.
    late = upload(ravi, ravi_s, work_id)
    assert late.status_code == 409  # type: ignore[attr-defined]
    assert late.json()["error"]["code"] == "not_preparing"  # type: ignore[attr-defined]

    # The assigner did not prepare it, so they may approve or return it.
    assert {"approve", "return"} <= set(actions(web, code))
    no_note = move(web, admin, code, "return", "in_review")
    assert no_note.status_code == 409
    assert no_note.json()["error"]["code"] == "note_required"
    _ok(move(web, admin, code, "return", "in_review", note="Input credit on page 2 is wrong"))
    assert stage(web, code) == "preparing"
    back = _ok(ravi.get(f"/api/consultant/assignments/{work_id}"))
    assert back["can_upload"] is True
    assert back["review"][-1]["action"] == "return"
    assert back["review"][-1]["note"] == "Input credit on page 2 is wrong"

    # Fixed, sent again, approved.
    assert upload(ravi, ravi_s, work_id).status_code == 201  # type: ignore[attr-defined]
    _ok(ravi.post(f"/api/consultant/assignments/{work_id}/submit", json={}, headers=ravi_s.headers))
    _ok(move(web, admin, code, "approve", "in_review", note="Looks right"))
    assert stage(web, code) == "ready_to_file"
    done = _ok(ravi.get(f"/api/consultant/assignments/{work_id}"))
    assert [r["action"] for r in done["review"]] == [
        "submit_for_review",
        "return",
        "submit_for_review",
        "approve",
    ]
    assert done["can_upload"] is False


# --- LL-094: the server refuses approval by the preparer -----------------------------------------


def test_the_preparer_cannot_approve_their_own_work(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: object,
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    for action, at in (
        ("assign", "documents_received"),
        ("start_preparing", "assigned"),
        ("submit_for_review", "preparing"),
    ):
        _ok(move(web, admin, code, action, at))

    # Not offered to them, with the reason shown.
    cycle = _ok(web.get(f"/api/tickets/{code}"))["cycle"]
    assert "approve" not in [m["action"] for m in cycle["moves"]]
    assert "return" in [m["action"] for m in cycle["moves"]]
    assert cycle["approve_blocked"] == "You prepared this work, so someone else must approve it."

    # Refused if attempted anyway, and the attempt is recorded.
    refused = move(web, admin, code, "approve", "in_review")
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "maker_checker"
    assert stage(web, code) == "in_review"
    logged = [e for e in entries(migrated_db) if e.event == "review.approval_refused"]
    assert [e.actor_user_id for e in logged] == [admin.user_id]

    # Someone else can.
    other, other_s = checker
    break_glass_ticket(other, other_s, code)  # not the checker's own client (C29)
    cycle = _ok(other.get(f"/api/tickets/{code}"))["cycle"]
    assert "approve" in [m["action"] for m in cycle["moves"]]
    assert cycle["approve_blocked"] is None
    _ok(move(other, other_s, code, "approve", "in_review"))
    assert stage(web, code) == "ready_to_file"


def test_being_returned_does_not_let_the_preparer_approve_the_second_round(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: object,
) -> None:
    """The preparer stays a preparer: a later round in someone else's hands does not clear it."""
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    other, other_s = checker
    break_glass_ticket(other, other_s, code)  # not the checker's own client (C29)
    _ok(move(web, admin, code, "assign", "documents_received"))
    _ok(move(web, admin, code, "start_preparing", "assigned"))
    _ok(move(web, admin, code, "submit_for_review", "preparing"))
    _ok(move(other, other_s, code, "return", "in_review", note="Recheck"))
    _ok(move(other, other_s, code, "submit_for_review", "preparing"))
    # Both have now prepared it, so neither may approve.
    for browser, signed in ((web, admin), (other, other_s)):
        refused = move(browser, signed, code, "approve", "in_review")
        assert refused.status_code == 403, refused.text


def test_staff_who_are_not_the_owner_cannot_approve_even_as_the_in_house_assignee(
    web: TestClient, admin: Signed, migrated_db: str, scanner: object
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF, name="Sita Staff")
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    _ok(assign(web, admin, code, sid))
    _ok(move(sita, sita_s, code, "start_preparing", "assigned"))
    _ok(move(sita, sita_s, code, "submit_for_review", "preparing"))
    assert _ok(sita.get(f"/api/tickets/{code}"))["cycle"]["approve_blocked"] == (
        "Only the client's owner or an Administrator can approve."
    )
    refused = move(sita, sita_s, code, "approve", "in_review")
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "maker_checker"
    # The owner, who did not prepare it, approves.
    _ok(move(web, admin, code, "approve", "in_review"))


def test_another_consultant_cannot_send_someone_elses_work_for_review(
    web: TestClient, admin: Signed, migrated_db: str, scanner: object
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    uid, ravi, ravi_s = consultant(web, migrated_db)
    _, mala, mala_s = consultant(web, migrated_db, "mala@consult.in")
    _ok(assign(web, admin, code, uid))
    work_id = _ok(ravi.get("/api/consultant/assignments"))[0]["id"]
    assert upload(ravi, ravi_s, work_id).status_code == 201  # type: ignore[attr-defined]
    stranger = mala.post(
        f"/api/consultant/assignments/{work_id}/submit", json={}, headers=mala_s.headers
    )
    assert stranger.status_code == 404
    assert stage(web, code) == "preparing"
