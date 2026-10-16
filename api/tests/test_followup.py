"""Chunk C34: nil filings (FR-CY-03) and phone follow-up (FR-IN-05).

Done when: the client's confirmation is shown to the reviewer; a promised date raises a reminder.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.followup import phone_promises
from app.models import Notification, NotificationKind, Role
from app.timeutil import today_ist
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_alerts import factory
from tests.test_checklist import open_portal, portal_upload, setup
from tests.test_clients import _ok, admin
from tests.test_documents import PDF, FakeScanner, scanner
from tests.test_tickets import move

__all__ = ["admin", "factory", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


def say_nil(portal: TestClient, csrf: str, note: str | None = "No sales or purchases") -> Any:
    return portal.post(
        "/api/portal/no-activity", json={"note": note}, headers={"X-CSRF-Token": csrf}
    )


def notifications(url: str, kind: NotificationKind) -> list[Notification]:
    with db_session(url) as db:
        rows = list(db.scalars(select(Notification).where(Notification.kind == kind)))
        for row in rows:
            db.expunge(row)
        return rows


# --- LL-138: the client confirms no activity; the cycle skips to review ------------------------


def test_the_client_confirms_no_activity_and_the_reviewer_sees_it(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup(web, admin, migrated_db)
    portal, csrf = open_portal(web, admin, code)
    assert _ok(portal.get("/api/portal/checklist"))["can_confirm_no_activity"] is True
    unverified = portal.post("/api/portal/no-activity", json={}, headers={"X-CSRF-Token": "x"})
    assert unverified.status_code == 403

    after = _ok(say_nil(portal, csrf))
    assert after["can_confirm_no_activity"] is False
    assert after["no_activity_confirmed_at"] is not None
    again = say_nil(portal, csrf)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_confirmed")

    ticket = _ok(web.get(f"/api/tickets/{code}"))
    cycle = ticket["cycle"]
    assert cycle["stage"] == "in_review"
    assert cycle["nil_confirmation"]["note"] == "No sales or purchases"
    assert "no_activity" not in [m["action"] for m in cycle["moves"]]
    assert [(h["action"], h["changed_by"]) for h in ticket["history"][:2]] == [
        ("no_activity", None),  # the system's move, on the client's word
        ("request_documents", None),
    ]
    assert "cycle.nil_confirmed" in [e.event for e in entries(migrated_db)]
    told = notifications(migrated_db, NotificationKind.NO_ACTIVITY)
    assert [n.user_id for n in told] == [admin.user_id]  # the owner

    # Nobody prepared it, so the owner may approve the nil filing.
    assert cycle["approve_blocked"] is None
    assert _ok(move(web, admin, code, "approve", "in_review"))["cycle"]["stage"] == "ready_to_file"


def test_only_the_client_can_skip_to_review_and_not_after_sending_documents(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    _, code = setup(web, admin, migrated_db)
    _ok(move(web, admin, code, "request_documents", "scheduled"))
    staff_try = move(web, admin, code, "no_activity", "awaiting_documents")
    assert (staff_try.status_code, staff_try.json()["error"]["code"]) == (
        409,
        "confirmation_required",
    )

    portal, csrf = open_portal(web, admin, code)
    item = _ok(web.get(f"/api/tickets/{code}/checklist"))[0]["id"]
    _ok(portal_upload(portal, csrf, item, "sales.pdf", PDF), 201)
    assert _ok(portal.get("/api/portal/checklist"))["can_confirm_no_activity"] is False
    refused = say_nil(portal, csrf)
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "documents_sent")
    assert _ok(web.get(f"/api/tickets/{code}"))["cycle"]["stage"] == "awaiting_documents"


def test_the_database_keeps_confirmations_as_given(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup(web, admin, migrated_db)
    portal, csrf = open_portal(web, admin, code)
    _ok(say_nil(portal, csrf, None))
    with db_session(migrated_db) as db:
        for sql, message in (
            ("UPDATE cycle_nil_confirmations SET note = 'x'", "never changed"),
            ("DELETE FROM cycle_nil_confirmations", "never deleted"),
        ):
            with pytest.raises(DBAPIError, match=message):
                db.execute(text(sql))
            db.rollback()


# --- LL-139: a phone call with a promised date raises a reminder on that date -----------------


def call(browser: TestClient, signed: Signed, code: str, note: str, days: int | None) -> Any:
    body: dict[str, Any] = {"note": note, "promised_on": None}
    if days is not None:
        body["promised_on"] = (today_ist() + timedelta(days=days)).isoformat()
    return browser.post(f"/api/tickets/{code}/phone-contacts", json=body, headers=signed.headers)


def test_a_promised_date_reminds_the_caller_once_while_still_waiting(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, code = setup(web, admin, migrated_db)
    for days, note in ((-1, "Too early"), (91, "Too late")):
        refused = call(web, admin, code, note, days)
        assert (refused.status_code, refused.json()["error"]["code"]) == (
            422,
            "bad_promised_date",
        )
    assert call(web, admin, code, "ok", None).status_code == 422  # too short a note
    logged = _ok(call(web, admin, code, "Rang Asha; will send registers", 2), 201)
    assert [(c["by"], c["reminder"]) for c in logged] == [("Test Person", None)]
    assert _ok(web.get(f"/api/tickets/{code}"))["phone_contacts"][0]["note"].startswith("Rang")
    audit = [e for e in entries(migrated_db) if e.event == "client.phone_contact"]
    assert "Asha" not in str(audit[0].details)  # the note stays out of the audit log

    today = today_ist()
    assert phone_promises(factory, today=today + timedelta(days=1)).reminded == 0  # not yet
    assert phone_promises(factory, today=today + timedelta(days=2)).reminded == 1
    assert phone_promises(factory, today=today + timedelta(days=3)).reminded == 0  # once only
    told = notifications(migrated_db, NotificationKind.PHONE_PROMISE)
    assert [n.user_id for n in told] == [admin.user_id]
    assert "will send registers" in told[0].body
    assert _ok(web.get(f"/api/tickets/{code}"))["phone_contacts"][0]["reminder"] == "reminded"
    with db_session(migrated_db) as db:
        for sql, message in (
            ("UPDATE phone_contacts SET note = 'x'", "never changed"),
            ("DELETE FROM phone_contacts", "never deleted"),
        ):
            with pytest.raises(DBAPIError, match=message):
                db.execute(text(sql))
            db.rollback()


def test_no_reminder_once_the_work_has_moved_on(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, code = setup(web, admin, migrated_db)
    _ok(call(web, admin, code, "Client says nothing this month", 0), 201)
    portal, csrf = open_portal(web, admin, code)
    _ok(say_nil(portal, csrf))
    tally = phone_promises(factory, today=today_ist())
    assert (tally.reminded, tally.settled) == (0, 1)
    assert notifications(migrated_db, NotificationKind.PHONE_PROMISE) == []
    assert _ok(web.get(f"/api/tickets/{code}"))["phone_contacts"][0]["reminder"] == "not needed"


def test_staff_log_calls_only_on_tickets_they_may_see(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup(web, admin, migrated_db)
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF)
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    assert call(sita, sita_s, code, "Not my client", 1).status_code == 403
