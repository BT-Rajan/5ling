"""Chunk C21: assignment and consultant access (BRD FR-WF-01, FR-WF-02, NFR-SE-02, FR-AU-01)."""

from __future__ import annotations

from typing import Any

import pytest
from app.models import (
    Assignment,
    Email,
    EmailKind,
    EndReason,
    Notification,
    NotificationKind,
    Role,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tests.audit_helpers import entries, events
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_checklist import CHECKLIST, publish_with_checklist
from tests.test_clients import _ok, admin, client_body, create
from tests.test_documents import PDF, FakeScanner, scanner
from tests.test_tickets import first_ticket, move

__all__ = ["admin", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


def to_documents_received(web: TestClient, admin: Signed, code: str) -> None:
    _ok(move(web, admin, code, "request_documents", "scheduled"))
    _ok(move(web, admin, code, "documents_received", "awaiting_documents"))


def setup(web: TestClient, admin: Signed, url: str) -> tuple[int, str, str]:
    """A client with two filings' worth of cycles; returns the client and two ticket codes."""
    pid, _ = publish_with_checklist(web, admin, CHECKLIST)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    code = first_ticket(url, cid)
    other = _ok(web.get("/api/tickets", params={"client_id": cid, "limit": 2}))["items"][1]["code"]
    return cid, code, other


def consultant(
    web: TestClient, url: str, email: str = "ravi@consult.in"
) -> tuple[int, TestClient, Signed]:
    uid = make_user(url, email, Role.CONSULTANT, name="Ravi Consultant")
    client = TestClient(web.app)
    return uid, client, sign_in_directly(url, client, uid)


def assign(web: TestClient, admin: Signed, code: str, assignee: int, **extra: Any) -> Any:
    return web.post(
        f"/api/tickets/{code}/assignment",
        json={"assignee_id": assignee, **extra},
        headers=admin.headers,
    )


def latest_email(url: str, kind: EmailKind) -> Email:
    with db_session(url) as db:
        email = db.scalar(
            select(Email).where(Email.kind == kind).order_by(Email.id.desc()).limit(1)
        )
        assert email is not None
        db.expunge(email)
        return email


# --- LL-089: consultants sign in by emailed link plus the code shown on screen -----------------


def test_a_consultant_signs_in_with_the_emailed_link_and_the_code_on_screen(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "ravi@consult.in", Role.CONSULTANT, name="Ravi Consultant")
    browser = TestClient(web.app)
    started = _ok(browser.post("/api/auth/consultant/start", json={"email": "Ravi@Consult.in"}))
    code = started["code"]
    assert len(code.replace(" ", "")) == 6
    email = latest_email(migrated_db, EmailKind.CONSULTANT_SIGN_IN)
    assert email.to_addresses == ["ravi@consult.in"]
    assert code not in email.body_text  # the code is never emailed
    token = email.body_text.split("/consultant/sign-in#t=", 1)[1].split()[0]

    phone = TestClient(web.app)
    wrong = phone.post("/api/auth/consultant/finish", json={"token": token, "code": "000000"})
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (401, "bad_code")
    signed = _ok(phone.post("/api/auth/consultant/finish", json={"token": token, "code": code}))
    assert signed["user"]["role"] == "consultant"
    assert _ok(phone.get("/api/auth/me"))["user"]["email"] == "ravi@consult.in"
    # The link works once.
    again = TestClient(web.app).post(
        "/api/auth/consultant/finish", json={"token": token, "code": code}
    )
    assert again.status_code == 401
    trail = events(migrated_db)
    assert "consultant.link_sent" in trail
    assert "consultant.code_failed" in trail
    assert "login.succeeded" in trail


def test_the_reply_never_tells_whether_an_address_is_a_consultant(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    make_user(migrated_db, "old@consult.in", Role.CONSULTANT, active=False)
    for address in (
        "nobody@nowhere.in",
        "boss@firm.in",
        "no-agreement@consult.in",
        "old@consult.in",
    ):
        reply = TestClient(web.app).post("/api/auth/consultant/start", json={"email": address})
        assert reply.status_code == 200
        assert set(reply.json()) == {"code", "minutes"}
    with db_session(migrated_db) as db:
        assert db.scalar(select(Email.id).where(Email.kind == EmailKind.CONSULTANT_SIGN_IN)) is None
    # And the password form stays closed to consultants.
    assert (
        web.post(
            "/api/auth/login", json={"email": "old@consult.in", "password": "x" * 12}
        ).status_code
        == 401
    )


# --- LL-088, LL-090: assigning work, and what the consultant then sees -------------------------


def test_assigning_moves_the_cycle_notifies_and_opens_only_that_cycle(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    cid, code, other_code = setup(web, admin, migrated_db)
    item = _ok(web.get(f"/api/tickets/{code}/checklist"))[0]["id"]
    _ok(
        web.post(
            f"/api/uploads/tickets/{code}",
            params={"item_id": item},
            content=PDF,
            headers={**admin.headers, "X-File-Name": "sales.pdf"},
        ),
        201,
    )
    to_documents_received(web, admin, code)
    uid, ravi, ravi_s = consultant(web, migrated_db)
    assert uid in [p["id"] for p in _ok(web.get("/api/assignment-candidates"))]

    made = _ok(assign(web, admin, code, uid, work_due_date="2099-01-31", note="Prepare the return"))
    assert made["current"]["assignee"] == {
        "id": uid,
        "full_name": "Ravi Consultant",
        "role": "consultant",
    }
    assert _ok(web.get(f"/api/tickets/{code}"))["cycle"]["stage"] == "assigned"
    with db_session(migrated_db) as db:
        note = db.scalar(select(Notification).where(Notification.user_id == uid))
        assert note is not None
        assert note.kind is NotificationKind.ASSIGNED
    assert "Prepare the return" in latest_email(migrated_db, EmailKind.ASSIGNMENT).body_text
    assert "assignment.created" in events(migrated_db)

    work = _ok(ravi.get("/api/consultant/assignments"))
    assert [(w["ticket"], w["work_due_date"], w["note"]) for w in work] == [
        (code, "2099-01-31", "Prepare the return")
    ]
    detail = _ok(ravi.get(f"/api/consultant/assignments/{work[0]['id']}"))
    version = detail["documents"][0]["versions"][0]["id"]
    link = _ok(ravi.post(f"/api/documents/versions/{version}/link", headers=ravi_s.headers))["url"]
    assert ravi.get(link).content == PDF
    uploaded = ravi.post(
        f"/api/uploads/consultant/assignments/{work[0]['id']}",
        content=PDF + b"%work",
        headers={**ravi_s.headers, "X-File-Name": "computation.pdf"},
    )
    assert uploaded.status_code == 201, uploaded.text
    titles = [(d["title"], d["versions"][0]["uploaded_via"]) for d in uploaded.json()]
    assert ("Work from Ravi Consultant", "consultant") in titles

    # Done when: any other client record is denied and logged.
    before = len([e for e in entries(migrated_db) if e.event == "access.denied"])
    assert ravi.get(f"/api/clients/{cid}").status_code == 403
    assert ravi.get(f"/api/tickets/{other_code}").status_code == 403
    assert ravi.get("/api/consultant/assignments/999999").status_code == 404
    other_item = _ok(web.get(f"/api/tickets/{other_code}/checklist"))[0]["id"]
    elsewhere = _ok(
        web.post(
            f"/api/uploads/tickets/{other_code}",
            params={"item_id": other_item},
            content=PDF,
            headers={**admin.headers, "X-File-Name": "x.pdf"},
        ),
        201,
    )[0]["versions"][0]["id"]
    assert (
        ravi.post(f"/api/documents/versions/{elsewhere}/link", headers=ravi_s.headers).status_code
        == 404
    )
    after = [e for e in entries(migrated_db) if e.event == "access.denied"]
    assert len(after) >= before + 4
    assert any(e.actor_user_id == uid for e in after)


def test_after_closure_the_consultant_has_no_access(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    uid, ravi, _ = consultant(web, migrated_db)
    _ok(assign(web, admin, code, uid))
    work_id = _ok(ravi.get("/api/consultant/assignments"))[0]["id"]
    for action, at in [
        ("start_preparing", "assigned"),
        ("submit_for_review", "preparing"),
        ("approve", "in_review"),
        ("file", "ready_to_file"),
        ("acknowledge", "filed"),
        ("close", "acknowledged"),
    ]:
        _ok(move(web, admin, code, action, at))
    closed = ravi.get(f"/api/consultant/assignments/{work_id}")
    assert (closed.status_code, closed.json()["error"]["code"]) == (403, "no_access")
    assert "no longer have access" in closed.json()["error"]["message"]
    assert _ok(ravi.get("/api/consultant/assignments")) == []
    with db_session(migrated_db) as db:
        ended = db.get(Assignment, work_id)
        assert ended is not None
        assert ended.end_reason is EndReason.CYCLE_CLOSED
    denied = [
        e for e in entries(migrated_db) if e.event == "access.denied" and e.actor_user_id == uid
    ]
    assert denied


def test_withdrawing_or_reassigning_ends_access_at_once(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    uid, ravi, _ = consultant(web, migrated_db)
    _ok(assign(web, admin, code, uid))
    work_id = _ok(ravi.get("/api/consultant/assignments"))[0]["id"]
    refused = web.post(
        f"/api/tickets/{code}/assignment/withdraw", json={"note": ""}, headers=admin.headers
    )
    assert refused.status_code == 422
    back = _ok(
        web.post(
            f"/api/tickets/{code}/assignment/withdraw",
            json={"note": "Client asked for in-house"},
            headers=admin.headers,
        )
    )
    assert back["current"] is None
    assert back["history"][0]["end_reason"] == "withdrawn"
    assert _ok(web.get(f"/api/tickets/{code}"))["cycle"]["stage"] == "documents_received"
    assert ravi.get(f"/api/consultant/assignments/{work_id}").json()["error"]["code"] == "no_access"

    # A new assignment replaces the open one; the database holds one open assignment a cycle.
    staff_id = make_user(migrated_db, "asha@firm.in", Role.STAFF, name="Asha Staff")
    _ok(assign(web, admin, code, uid))
    replaced = _ok(assign(web, admin, code, staff_id))
    assert replaced["current"]["assignee"]["id"] == staff_id
    assert replaced["history"][1]["end_reason"] == "reassigned"
    assert assign(web, admin, code, staff_id).json()["error"]["code"] == "already_assigned"
    with db_session(migrated_db) as db, pytest.raises(Exception, match="Duplicate"):
        db.execute(
            text(
                "INSERT INTO assignments (cycle_id, assignee_id, assigned_by_id, assigned_at) "
                "SELECT cycle_id, assignee_id, assigned_by_id, assigned_at FROM assignments "
                "WHERE ended_at IS NULL"
            )
        )
    with db_session(migrated_db) as db, pytest.raises(Exception, match="cannot be changed"):
        db.execute(text("UPDATE assignments SET ended_at = NULL WHERE ended_at IS NOT NULL"))


def test_the_in_house_assignee_can_work_on_that_cycle_only(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    _, code, other = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    staff_id = make_user(migrated_db, "asha@firm.in", Role.STAFF, name="Asha Staff")
    asha = TestClient(web.app)
    asha_s = sign_in_directly(migrated_db, asha, staff_id)
    assert asha.get(f"/api/tickets/{code}").status_code == 403
    _ok(assign(web, admin, code, staff_id))
    assert _ok(asha.get(f"/api/tickets/{code}"))["can_edit"] is False
    assert _ok(asha.get(f"/api/tickets/{code}/documents")) == []
    assert (
        _ok(move(asha, asha_s, code, "start_preparing", "assigned"))["cycle"]["stage"]
        == "preparing"
    )
    assert (
        asha.get(f"/api/tickets/{code}/links").status_code == 403
    )  # the client's link is the owner's
    assert asha.get(f"/api/tickets/{other}").status_code == 403
    assert (
        asha.post(
            f"/api/tickets/{code}/assignment",
            json={"assignee_id": staff_id},
            headers=asha_s.headers,
        ).status_code
        == 403
    )


def test_the_stage_moves_for_assignment_come_only_from_assigning(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    to_documents_received(web, admin, code)
    direct = web.post(
        f"/api/tickets/{code}/transition",
        json={"action": "assign", "expected_stage": "documents_received"},
        headers=admin.headers,
    )
    assert direct.json()["error"]["code"] == "use_assignment"
    gone = make_user(migrated_db, "gone@consult.in", Role.CONSULTANT, active=False)
    assert assign(web, admin, code, gone).json()["error"]["code"] == "bad_assignee"
    assert assign(web, admin, code, 999999).json()["error"]["code"] == "bad_assignee"
