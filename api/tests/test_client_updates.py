"""Chunk C15: manual state-change emails (CR-03, FR-AC-01, FR-CL-03, FR-AU-01)."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from app.cycles.service import emit_state_event
from app.db import make_engine, make_session_factory
from app.mail.provider import RecordingProvider
from app.mail.updates import sweep
from app.mail.worker import send_due
from app.models import Client, Email, EmailKind, EmailStatus, EventKind, Role, StateEvent
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_clients import _ok, admin, client_body, create, publish_package
from tests.test_tickets import checker, first_ticket, move

__all__ = ["admin", "checker"]  # the fixture, re-used from test_clients

pytestmark = pytest.mark.requires_db

PATH = [
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


def with_contacts(pid: int, owner_id: int) -> dict[str, Any]:
    return client_body(
        pid,
        owner_id=owner_id,
        contacts=[
            {"name": "Asha Rao", "email": "asha@example.in", "is_primary": True},
            {"name": "Ravi Rao", "email": "ravi@example.in"},
            {"name": "Accounts", "email": "accounts@example.in"},
        ],
    )


def setup(web: TestClient, admin: Signed, url: str) -> tuple[int, str]:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, with_contacts(pid, admin.user_id)), 201)["client_id"]
    return cid, first_ticket(url, cid)


def updates(url: str) -> list[Email]:
    with db_session(url) as db:
        return list(
            db.scalars(
                select(Email).where(Email.kind == EmailKind.CLIENT_UPDATE).order_by(Email.id)
            )
        )


# --- Done when: no email leaves the system without a click; each state change yields exactly
# one draft --------------------------------------------------------------------------------------


def test_every_move_makes_one_draft_and_nothing_is_sent(
    web: TestClient, admin: Signed, migrated_db: str, checker: tuple[TestClient, Signed]
) -> None:
    _, code = setup(web, admin, migrated_db)
    for action, at in PATH:
        _ok(move(*(checker if action == "approve" else (web, admin)), code, action, at))
    drafts = updates(migrated_db)
    assert len(drafts) == 9
    assert {d.status for d in drafts} == {EmailStatus.DRAFT}
    assert all(d.to_addresses == ["asha@example.in"] for d in drafts)
    with db_session(migrated_db) as db:
        assert db.scalar(select(func.count()).where(StateEvent.handled_at.is_(None))) == 0
        assert sorted(d.state_event_id or 0 for d in drafts) == sorted(
            db.scalars(select(StateEvent.id))
        )
    listed = _ok(web.get(f"/api/tickets/{code}/updates"))
    assert len(listed) == 9
    assert (listed[0]["from_state"], listed[0]["to_state"]) == ("acknowledged", "closed")
    assert listed[-1]["subject"].endswith(f"Awaiting documents ({code})")

    # The send worker never sends a draft.
    engine = make_engine(migrated_db)
    try:
        factory = make_session_factory(engine)
        provider = RecordingProvider()
        assert send_due(factory, provider).handled == 0
        assert sweep(factory, "Firm") == 0  # nothing left to draft, nothing drafted twice
    finally:
        engine.dispose()
    assert provider.sent == []
    assert {d.status for d in updates(migrated_db)} == {EmailStatus.DRAFT}


def test_a_draft_is_previewed_then_sent_once_with_a_note_and_chosen_copies(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup(web, admin, migrated_db)
    _ok(move(web, admin, code, "request_documents", "scheduled"))
    draft = _ok(web.get(f"/api/tickets/{code}/updates"))[0]
    preview = _ok(
        web.post(
            f"/api/updates/{draft['id']}/preview",
            json={"note": "Please send the sales register by Friday."},
            headers=admin.headers,
        )
    )
    assert preview["to"] == ["asha@example.in"]
    assert preview["cc"] == []
    assert [c["name"] for c in preview["other_contacts"]] == ["Ravi Rao", "Accounts"]
    assert "Please send the sales register by Friday." in preview["body"]
    assert "Scheduled to Awaiting documents" in preview["body"]
    assert updates(migrated_db)[0].status is EmailStatus.DRAFT  # a preview changes nothing

    ravi = preview["other_contacts"][0]["id"]
    body = {"note": "Please send the sales register by Friday.", "cc_contact_ids": [ravi]}
    sent = _ok(web.post(f"/api/updates/{draft['id']}/send", json=body, headers=admin.headers))
    assert (sent["status"], sent["to"], sent["cc"]) == (
        "queued",
        ["asha@example.in"],
        ["ravi@example.in"],
    )
    assert sent["sent_by"] == "Test Person"

    # A second click (or a second person) sends nothing more.
    again = web.post(f"/api/updates/{draft['id']}/send", json=body, headers=admin.headers)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "already_sent"
    stored = updates(migrated_db)[0]
    assert "Test Person" in stored.body_text
    assert "sales register" in stored.body_text

    sends = [e for e in entries(migrated_db) if e.event == "client_update.sent"]
    assert len(sends) == 1
    assert sends[0].details["copied"] == 1
    assert "ravi@example.in" not in str(sends[0].details)  # no addresses in the audit log


def test_double_clicking_send_queues_one_email(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup(web, admin, migrated_db)
    _ok(move(web, admin, code, "request_documents", "scheduled"))
    draft = _ok(web.get(f"/api/tickets/{code}/updates"))[0]
    results: list[int] = []

    def click() -> None:
        results.append(
            web.post(f"/api/updates/{draft['id']}/send", json={}, headers=admin.headers).status_code
        )

    clicks = [threading.Thread(target=click) for _ in range(4)]
    for t in clicks:
        t.start()
    for t in clicks:
        t.join()
    assert sorted(results) == [200, 409, 409, 409]
    assert [e.event for e in entries(migrated_db)].count("client_update.sent") == 1


def test_client_status_and_task_changes_make_drafts_too(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _ = setup(web, admin, migrated_db)
    _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"status": "paused", "reason": "Client travelling"},
            headers=admin.headers,
        )
    )
    status = _ok(web.get(f"/api/clients/{cid}/updates"))
    assert [(u["from_state"], u["to_state"], u["status"]) for u in status] == [
        ("active", "paused", "draft")
    ]
    assert "is now Paused" in status[0]["subject"]

    task = _ok(
        web.post(
            "/api/tickets",
            json={"client_id": cid, "title": "Collect signed engagement letter"},
            headers=admin.headers,
        ),
        201,
    )["code"]
    _ok(web.patch(f"/api/tickets/{task}", json={"status": "done"}, headers=admin.headers))
    drafts = _ok(web.get(f"/api/tickets/{task}/updates"))
    assert [(u["from_state"], u["to_state"]) for u in drafts] == [("open", "done")]
    assert drafts[0]["subject"] == f"Collect signed engagement letter: Done ({task})"
    # Status updates are not listed against a ticket, nor ticket updates against the client.
    assert len(_ok(web.get(f"/api/clients/{cid}/updates"))) == 1


def test_only_the_owner_or_the_administrator_sees_and_sends_updates(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    owner_id = make_user(migrated_db, "owner@firm.in", Role.STAFF, name="Owner Person")
    other_id = make_user(migrated_db, "other@firm.in", Role.STAFF)
    cid = _ok(create(web, admin, with_contacts(pid, owner_id)), 201)["client_id"]
    code = first_ticket(migrated_db, cid)
    owner = TestClient(web.app)
    signed_owner = sign_in_directly(migrated_db, owner, owner_id)
    other = TestClient(web.app)
    signed_other = sign_in_directly(migrated_db, other, other_id)

    _ok(move(owner, signed_owner, code, "request_documents", "scheduled"))
    draft = _ok(owner.get(f"/api/tickets/{code}/updates"))[0]
    assert other.get(f"/api/tickets/{code}/updates").status_code == 403
    assert other.get(f"/api/clients/{cid}/updates").status_code == 403
    for path in ("preview", "send"):
        refused = other.post(
            f"/api/updates/{draft['id']}/{path}", json={}, headers=signed_other.headers
        )
        assert refused.status_code == 403, path
    assert updates(migrated_db)[0].status is EmailStatus.DRAFT
    sent = owner.post(f"/api/updates/{draft['id']}/send", json={}, headers=signed_owner.headers)
    assert _ok(sent)["sent_by"] == "Owner Person"
    assert web.post("/api/updates/999999/send", json={}, headers=admin.headers).status_code == 404


def test_only_this_clients_contacts_can_be_copied(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup(web, admin, migrated_db)
    _ok(move(web, admin, code, "request_documents", "scheduled"))
    draft = _ok(web.get(f"/api/tickets/{code}/updates"))[0]
    bad = web.post(
        f"/api/updates/{draft['id']}/send",
        json={"cc_contact_ids": [999999]},
        headers=admin.headers,
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "bad_contact"
    assert updates(migrated_db)[0].status is EmailStatus.DRAFT


def test_the_worker_drafts_any_change_that_missed_its_draft(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _ = setup(web, admin, migrated_db)
    with db_session(migrated_db) as db:
        pk = db.scalar(select(Client.id).where(Client.client_id == cid))
        assert pk is not None
        emit_state_event(
            db,
            EventKind.CLIENT_STATUS,
            client_pk=pk,
            ticket_id=None,
            from_state="active",
            to_state="paused",
        )
        db.commit()
    engine = make_engine(migrated_db)
    try:
        factory = make_session_factory(engine)
        assert sweep(factory, "Firm") == 1
        assert sweep(factory, "Firm") == 0
    finally:
        engine.dispose()
    assert len(updates(migrated_db)) == 1
