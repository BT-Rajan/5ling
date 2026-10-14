"""Chunk C18: scheduled client alerts and reminders (BRD FR-AL-01, FR-AL-03, FR-AL-06)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime, time, timedelta
from urllib.parse import quote

import pytest
from app.alerts.job import due_kind, run
from app.db import make_engine, make_session_factory
from app.mail.provider import PermanentFailure, RecordingProvider, TemporaryFailure
from app.mail.worker import send_due
from app.models import AlertKind, Cycle, CycleAlert, Email, EmailKind, Ticket
from app.timeutil import IST
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import events
from tests.helpers import Signed, db_session
from tests.test_checklist import CHECKLIST, publish_with_checklist
from tests.test_clients import _ok, admin, client_body, create
from tests.test_documents import PDF, FakeScanner, scanner

__all__ = ["admin", "scanner"]  # fixtures re-used from other test modules


# --- When an alert is due ------------------------------------------------------------------


def test_the_first_alert_then_reminders_every_two_days_then_daily() -> None:
    alert, due = date(2026, 5, 7), date(2026, 5, 11)

    def kind(today: date, last: date | None) -> AlertKind | None:
        return due_kind(
            alert_date=alert, due_date=due, last_sent=last, today=today, interval_days=2
        )

    assert kind(date(2026, 5, 6), None) is None
    assert kind(alert, None) is AlertKind.FIRST
    assert kind(date(2026, 5, 9), None) is AlertKind.FIRST  # late, but still the first
    assert kind(alert, alert) is None  # never twice a day
    assert kind(date(2026, 5, 8), alert) is None
    assert kind(date(2026, 5, 9), alert) is AlertKind.REMINDER
    assert kind(date(2026, 5, 10), date(2026, 5, 9)) is None
    assert kind(date(2026, 5, 12), date(2026, 5, 11)) is AlertKind.REMINDER  # past due: daily
    assert kind(date(2026, 5, 13), date(2026, 5, 12)) is AlertKind.REMINDER


# --- With a database ---------------------------------------------------------------------------


@pytest.fixture
def factory(migrated_db: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(migrated_db)
    yield make_session_factory(engine)
    engine.dispose()


def first_cycle(url: str) -> Cycle:
    with db_session(url) as db:
        cycle = db.scalar(select(Cycle).order_by(Cycle.period_start).limit(1))
        assert cycle is not None
        db.expunge(cycle)
        return cycle


def at(day: date, hour: int = 10) -> datetime:
    return datetime.combine(day, time(hour), IST)


def alerts_of(url: str, cycle_id: int) -> list[tuple[str, date]]:
    with db_session(url) as db:
        return [
            (a.kind.value, a.sent_on)
            for a in db.scalars(
                select(CycleAlert).where(CycleAlert.cycle_id == cycle_id).order_by(CycleAlert.id)
            )
        ]


def setup(web: TestClient, admin: Signed, url: str) -> tuple[int, Cycle, str]:
    pid, _ = publish_with_checklist(web, admin, CHECKLIST)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    cycle = first_cycle(url)
    with db_session(url) as db:
        number = db.scalar(select(Ticket.number).where(Ticket.id == cycle.ticket_id))
    return cid, cycle, f"TKT-{number:06d}"


# --- Done when: alert sent once on the computed date; reminders stop within one hour of a
# complete checklist ----------------------------------------------------------------------------


@pytest.mark.requires_db
def test_the_alert_goes_once_on_its_date_with_a_link_and_the_checklist(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, cycle, code = setup(web, admin, migrated_db)
    settings = web.app.state.settings  # type: ignore[attr-defined]
    day = cycle.alert_date
    run(factory, settings, now=at(day - timedelta(days=1)))
    assert alerts_of(migrated_db, cycle.id) == []
    tally = run(factory, settings, now=at(day))
    assert tally.sent >= 1
    assert alerts_of(migrated_db, cycle.id) == [("first", day)]
    run(factory, settings, now=at(day, 15))  # later the same day: nothing more
    assert alerts_of(migrated_db, cycle.id) == [("first", day)]

    with db_session(migrated_db) as db:
        email = db.scalar(
            select(Email).where(
                Email.kind == EmailKind.CLIENT_ALERT, Email.ticket_id == cycle.ticket_id
            )
        )
        assert email is not None
    assert email.to_addresses == ["asha@example.in"]  # the primary contact
    assert email.status.value == "queued"
    assert "/portal#t=" in email.body_text
    for label in ("Sales register", "Purchase register", "Notes for us (if you have it)"):
        assert f"- {label}" in email.body_text
    assert code in email.subject
    assert "alert.sent" in events(migrated_db)
    # Outside the sending hours nothing is queued at all.
    assert (
        run(factory, settings, now=at(day + timedelta(days=5), 22)).skipped_reason
        == "outside sending hours"
    )


@pytest.mark.requires_db
def test_reminders_repeat_then_stop_once_the_checklist_is_complete(
    web: TestClient,
    admin: Signed,
    migrated_db: str,
    factory: sessionmaker[Session],
    scanner: FakeScanner,
) -> None:
    _, cycle, code = setup(web, admin, migrated_db)
    settings = web.app.state.settings  # type: ignore[attr-defined]
    day = cycle.alert_date
    for offset in range(0, 5):
        run(factory, settings, now=at(day + timedelta(days=offset)))
    sent = alerts_of(migrated_db, cycle.id)
    assert sent[:3] == [
        ("first", day),
        ("reminder", day + timedelta(days=2)),
        ("reminder", day + timedelta(days=4)),
    ]
    after_due = cycle.due_date + timedelta(days=1)
    run(factory, settings, now=at(after_due))
    run(factory, settings, now=at(after_due + timedelta(days=1)))
    assert alerts_of(migrated_db, cycle.id)[-2:] == [
        ("reminder", after_due),
        ("reminder", after_due + timedelta(days=1)),
    ]
    with db_session(migrated_db) as db:
        last = db.scalar(
            select(Email)
            .where(Email.ticket_id == cycle.ticket_id)
            .order_by(Email.id.desc())
            .limit(1)
        )
        assert last is not None
        assert "The due date has passed" in last.body_text

    # The two required documents arrive (the optional one need not): reminders stop.
    items = _ok(web.get(f"/api/tickets/{code}/checklist"))
    for item in items[:2]:
        _ok(
            web.post(
                f"/api/uploads/tickets/{code}",
                params={"item_id": item["id"]},
                content=PDF,
                headers={**admin.headers, "X-File-Name": quote("doc.pdf")},
            ),
            201,
        )
    before = alerts_of(migrated_db, cycle.id)
    run(factory, settings, now=at(after_due + timedelta(days=2)))
    assert alerts_of(migrated_db, cycle.id) == before


@pytest.mark.requires_db
def test_no_alerts_for_paused_clients_or_work_past_document_collection(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    cid, cycle, _ = setup(web, admin, migrated_db)
    settings = web.app.state.settings  # type: ignore[attr-defined]
    _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"status": "paused", "reason": "Away"},
            headers=admin.headers,
        )
    )
    run(factory, settings, now=at(cycle.alert_date))
    assert alerts_of(migrated_db, cycle.id) == []
    _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"status": "active", "reason": "Back"},
            headers=admin.headers,
        )
    )
    with db_session(migrated_db) as db:
        db.execute(
            text("UPDATE cycles SET stage = 'documents_received' WHERE id = :i"), {"i": cycle.id}
        )
        db.commit()
    run(factory, settings, now=at(cycle.alert_date))
    assert alerts_of(migrated_db, cycle.id) == []


# --- FR-AL-06: a failed send appears on the owner's work list ---------------------------------


@pytest.mark.requires_db
def test_a_failed_alert_shows_on_the_owners_work_list_until_followed_up(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, cycle, code = setup(web, admin, migrated_db)
    settings = web.app.state.settings  # type: ignore[attr-defined]
    run(factory, settings, now=at(cycle.alert_date))
    assert _ok(web.get("/api/tickets", params={"email_problems": True}))["total"] == 0

    send_due(
        factory, RecordingProvider(fail_with=TemporaryFailure("could not reach the mail server"))
    )
    delayed = _ok(web.get("/api/tickets", params={"email_problems": True}))
    assert code in [r["code"] for r in delayed["items"]]
    row = next(r for r in delayed["items"] if r["code"] == code)
    assert row["email_problem"] == "delayed"

    with db_session(migrated_db) as db:
        email_id = db.scalar(
            select(Email.id).where(
                Email.ticket_id == cycle.ticket_id, Email.kind == EmailKind.CLIENT_ALERT
            )
        )
        db.execute(
            text("UPDATE emails SET next_attempt_at = UTC_TIMESTAMP(6) WHERE id = :i"),
            {"i": email_id},
        )
        db.commit()
    send_due(factory, RecordingProvider(fail_with=PermanentFailure("the mail server replied 550")))
    failed = _ok(web.get("/api/tickets", params={"email_problems": True, "limit": 100}))
    row = next(r for r in failed["items"] if r["code"] == code)
    assert (row["email_problem"], row["email_problem_label"]) == (
        "failed",
        "Email to the client failed: call them",
    )
    emails = _ok(web.get(f"/api/tickets/{code}/emails"))
    assert [(e["kind_label"], e["status"], e["problem"]) for e in emails] == [
        ("Alert", "failed", "failed")
    ]

    done = _ok(web.post(f"/api/emails/{email_id}/followed-up", headers=admin.headers))
    assert (done["problem"], done["followed_up_by"]) == (None, "Test Person")
    after = _ok(web.get("/api/tickets", params={"email_problems": True, "limit": 100}))
    assert code not in [r["code"] for r in after["items"]]
    again = web.post(f"/api/emails/{email_id}/followed-up", headers=admin.headers)
    assert again.json()["error"]["code"] == "no_problem"
    assert "email.followed_up" in events(migrated_db)
