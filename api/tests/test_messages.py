"""Chunk C35: client messages (FR-AL-07), the completion message (FR-WF-06, CR-03) and fee
reminders (FR-FE-07).

Done when: a client message notifies the owner; the completion email is a manual draft.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.fees.reminders import MAX_REMINDERS, send_fee_reminders
from app.models import Email, EmailKind, EmailStatus, FeeReminder, Notification, NotificationKind
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session
from tests.test_assignments import setup
from tests.test_checklist import open_portal, portal_upload
from tests.test_clients import _ok, admin
from tests.test_documents import PDF, FakeScanner, scanner
from tests.test_fees import factory
from tests.test_filing import file_it, ready_to_file
from tests.test_payments import invoiced, pay
from tests.test_tickets import checker, move

__all__ = ["admin", "checker", "factory", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


def write(portal: TestClient, csrf: str, body: str) -> Any:
    return portal.post("/api/portal/messages", json={"body": body}, headers={"X-CSRF-Token": csrf})


# --- LL-140: a portal reply is kept against the cycle and the owner is told -------------------


def test_a_client_message_is_kept_on_the_cycle_and_the_owner_is_told(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    portal, csrf = open_portal(web, admin, code)
    assert write(portal, "wrong", "Hello").status_code == 403
    assert write(portal, csrf, "   ").status_code == 422
    sent = _ok(write(portal, csrf, "Sales register is with my accountant <b>today</b>"), 201)
    assert [m["body"] for m in sent["messages"]] == [
        "Sales register is with my accountant <b>today</b>"
    ]
    assert sent["can_message"] is True

    ticket = _ok(web.get(f"/api/tickets/{code}"))
    assert [m["body"] for m in ticket["messages"]] == [
        "Sales register is with my accountant <b>today</b>"
    ]
    with db_session(migrated_db) as db:
        told = list(
            db.scalars(
                select(Notification).where(Notification.kind == NotificationKind.CLIENT_MESSAGE)
            )
        )
        assert [n.user_id for n in told] == [admin.user_id]
        assert "with my accountant" in told[0].body
        for sql, message in (
            ("UPDATE client_messages SET body = 'x'", "never changed"),
            ("DELETE FROM client_messages", "never deleted"),
        ):
            with pytest.raises(Exception, match=message):
                db.execute(text(sql))
            db.rollback()
    logged = [e for e in entries(migrated_db) if e.event == "portal.message_received"]
    assert len(logged) == 1
    assert "accountant" not in str(logged[0].details)  # what it says stays out of the audit


def test_a_client_cannot_flood_the_firm_with_messages(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    portal, csrf = open_portal(web, admin, code)
    for n in range(20):
        _ok(write(portal, csrf, f"Message {n}"), 201)
    refused = write(portal, csrf, "One more")
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "too_many")


# --- LL-141: the filed update is a manual draft that carries the acknowledgement link ----------


def filed(web: TestClient, admin: Signed, checker: tuple[TestClient, Signed], url: str) -> str:
    _, code, _ = setup(web, admin, url)
    ready_to_file(web, admin, checker, code)
    _ok(file_it(web, admin, code, "AA0705260012345"))
    return code


def test_the_filed_update_is_a_draft_until_sent_then_carries_one_private_link(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: FakeScanner,
) -> None:
    code = filed(web, admin, checker, migrated_db)
    drafts = _ok(web.get(f"/api/tickets/{code}/updates"))
    done = next(u for u in drafts if u["to_state"] == "filed")
    assert done["status"] == "draft"  # CR-03: nothing goes until a person sends it
    assert done["subject"].endswith(": filed (" + code + ")")
    preview = _ok(web.post(f"/api/updates/{done['id']}/preview", json={}, headers=admin.headers))
    assert "[your private link is added when this is sent]" in preview["body"]
    assert "AA0705260012345" in preview["body"]

    _ok(web.post(f"/api/updates/{done['id']}/send", json={}, headers=admin.headers))
    again = web.post(f"/api/updates/{done['id']}/send", json={}, headers=admin.headers)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_sent")
    with db_session(migrated_db) as db:
        email = db.get(Email, done["id"])
        assert email is not None
        assert email.status is EmailStatus.QUEUED
        body = email.body_text
    assert "/portal#t=" in body
    token = body.split("#t=", 1)[1].split()[0]

    # The client opens it and downloads the acknowledgement; nothing more can be uploaded.
    portal = TestClient(web.app)
    csrf = _ok(portal.post("/api/portal/open", json={"token": token}))["csrf_token"]
    view = _ok(portal.get("/api/portal/checklist"))
    assert view["finished"] is True
    assert view["acknowledgement"]["reference"] == "AA0705260012345"
    got = portal.get("/api/portal/acknowledgement")
    assert got.status_code == 200
    assert got.content.startswith(b"%PDF")
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-disposition"].startswith("attachment;")
    item = _ok(web.get(f"/api/tickets/{code}/checklist"))[0]["id"]
    late = portal_upload(portal, csrf, item, "late.pdf", PDF)
    assert (late.status_code, late.json()["error"]["code"]) == (409, "finished")
    downloads = [e for e in entries(migrated_db) if e.event == "document.downloaded"]
    assert downloads[-1].actor_role == "client"

    # Closing the cycle keeps the acknowledgement available for the link's time, read-only.
    _ok(move(web, admin, code, "acknowledge", "filed"))
    _ok(move(web, admin, code, "close", "acknowledged"))
    assert portal.get("/api/portal/acknowledgement").status_code == 200
    closed = write(portal, csrf, "Thank you")
    assert (closed.status_code, closed.json()["error"]["code"]) == (409, "closed")


# --- LL-142: fee reminders at set intervals after the due date, stopping when paid -------------


def reminders(url: str) -> list[FeeReminder]:
    with db_session(url) as db:
        rows = list(db.scalars(select(FeeReminder).order_by(FeeReminder.id)))
        for row in rows:
            db.expunge(row)
        return rows


def test_fee_reminders_go_at_the_interval_and_stop_when_paid(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, inv = invoiced(web, admin, factory, admin.user_id)
    with db_session(migrated_db) as db:
        due = db.scalar(text("SELECT due_date FROM invoices WHERE id = :i"), {"i": inv})
    assert due is not None

    def run(day: int) -> int:
        return send_fee_reminders(factory, today=due + timedelta(days=day), firm_name="Firm").sent

    assert run(0) == 0  # due today: not yet overdue
    assert run(6) == 0  # the first reminder is 7 days after the due date (the default rule)
    assert run(7) == 1
    assert run(7) == 0  # once a day at most, however often the job runs
    assert run(13) == 0
    assert run(14) == 1
    sent = reminders(migrated_db)
    assert [r.sent_on for r in sent] == [due + timedelta(days=7), due + timedelta(days=14)]
    assert sent[0].balance_paise == 118000
    with db_session(migrated_db) as db:
        email = db.get(Email, sent[0].email_id)
        assert email is not None
        assert (email.kind, email.status) == (EmailKind.FEE_REMINDER, EmailStatus.QUEUED)
        assert email.to_addresses == ["c1@example.in"]  # the primary contact
        assert "Rs. 1180.00 owing" in email.body_text

    # Part paid: still reminded, for what is left. Paid in full: never again.
    _ok(pay(web, admin, inv, "180"), 201)
    assert run(21) == 1
    assert reminders(migrated_db)[-1].balance_paise == 100000
    _ok(pay(web, admin, inv, "1000"), 201)
    assert run(28) == 0
    assert run(60) == 0


def test_reminders_stop_after_the_most_and_follow_the_rule(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, inv = invoiced(web, admin, factory, admin.user_id)
    with db_session(migrated_db) as db:
        due = db.scalar(text("SELECT due_date FROM invoices WHERE id = :i"), {"i": inv})
        db.execute(text("UPDATE rule_settings SET fee_reminder_interval_days = 1"))
        db.commit()
    assert due is not None
    total = sum(
        send_fee_reminders(factory, today=due + timedelta(days=d), firm_name="F").sent
        for d in range(1, 15)
    )
    assert total == MAX_REMINDERS


def test_closed_clients_are_not_reminded(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    cid, inv = invoiced(web, admin, factory, admin.user_id)
    _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"status": "closed", "reason": "Engagement ended"},
            headers=admin.headers,
        )
    )
    with db_session(migrated_db) as db:
        due = db.scalar(text("SELECT due_date FROM invoices WHERE id = :i"), {"i": inv})
    assert due is not None
    assert send_fee_reminders(factory, today=due + timedelta(days=7), firm_name="F").sent == 0
