"""Chunk C36: WhatsApp or SMS beside the email alerts (BRD INT-02, FR-AL-08, C-2).

Done when: an opted-out channel is never used; failure falls back to email.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.alerts.job import run
from app.config import Settings
from app.mail.provider import PermanentFailure, TemporaryFailure
from app.messaging.provider import LogOnlyProvider, RecordingProvider, provider_from_settings
from app.messaging.service import choose_channel, dispatch, queue_for_alert
from app.messaging.templates import TEMPLATES, render_message
from app.models import (
    Channel,
    ChannelMessage,
    Client,
    Contact,
    Email,
    EmailKind,
    MessageStatus,
    Notification,
    NotificationKind,
)
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import make_settings
from tests.helpers import Signed, db_session
from tests.test_alerts import at, factory, first_cycle
from tests.test_checklist import CHECKLIST, publish_with_checklist
from tests.test_clients import _ok, admin, client_body, create

__all__ = ["admin", "factory"]  # fixtures re-used from other test modules

APPROVED = "first_alert,reminder"


# --- Choosing the channel ------------------------------------------------------------------------


def test_the_channel_follows_consent_number_and_approval() -> None:
    both = {Channel.EMAIL: True, Channel.WHATSAPP: True, Channel.SMS: True}
    sms_only = {Channel.EMAIL: True, Channel.WHATSAPP: False, Channel.SMS: True}
    none = {Channel.EMAIL: True, Channel.WHATSAPP: False, Channel.SMS: False}
    approved = {"first_alert", "reminder"}
    assert choose_channel(both, "+919876543210", "first_alert", approved) is Channel.WHATSAPP
    assert choose_channel(sms_only, "+919876543210", "first_alert", approved) is Channel.SMS
    assert choose_channel(none, "+919876543210", "first_alert", approved) is None  # opted out
    assert choose_channel(both, None, "first_alert", approved) is None  # no number
    assert choose_channel(both, "+919876543210", "first_alert", {"reminder"}) is None  # C-2


def test_templates_are_short_and_carry_no_link() -> None:
    values = {
        "firm_name": "Rao & Co",
        "client_name": "Asha\nRao",
        "filing": "GSTR-1",
        "period": "Apr 2026",
        "due_date": "Mon 11 May 2026",
        "ticket_id": "TKT-000001",
    }
    for key in TEMPLATES:
        body, params = render_message(key, values)
        assert len(body) <= 300
        assert "http" not in body  # the private link only goes by email
        assert "Asha Rao" in params  # one line per value, as WhatsApp templates require


def test_the_provider_is_off_until_chosen() -> None:
    assert provider_from_settings(make_settings()) is None
    assert isinstance(
        provider_from_settings(make_settings(messaging_provider="log")), LogOnlyProvider
    )


# --- Through the alerts job -------------------------------------------------------------------


def client_with(web: TestClient, admin: Signed, *, whatsapp: bool, sms: bool) -> int:
    pid, _ = publish_with_checklist(web, admin, CHECKLIST)
    body = client_body(pid, owner_id=admin.user_id)
    body["contacts"][0] |= {"whatsapp": whatsapp, "sms": sms}
    return int(_ok(create(web, admin, body), 201)["client_id"])


def settings_for(url: str, **over: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": url,
        "messaging_provider": "log",
        "messaging_approved_templates": APPROVED,
    }
    return make_settings(**(values | over))


def messages(url: str) -> list[ChannelMessage]:
    with db_session(url) as db:
        rows = list(db.scalars(select(ChannelMessage).order_by(ChannelMessage.id)))
        for row in rows:
            db.expunge(row)
        return rows


def alert_emails(url: str) -> int:
    with db_session(url) as db:
        return len(list(db.scalars(select(Email).where(Email.kind == EmailKind.CLIENT_ALERT))))


@pytest.mark.requires_db
def test_an_agreed_client_gets_whatsapp_beside_the_email(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    client_with(web, admin, whatsapp=True, sms=True)
    cycle = first_cycle(migrated_db)
    phone = RecordingProvider()
    run(factory, settings_for(migrated_db), now=at(cycle.alert_date), messenger=phone)
    assert alert_emails(migrated_db) == 1  # email always goes
    [sent] = phone.sent
    assert (sent.channel, sent.template, sent.to) == (
        Channel.WHATSAPP,
        "first_alert",
        "+919876543210",
    )
    assert sent.params[1] == "Asha Rao"
    [row] = messages(migrated_db)
    assert (row.status, row.attempts, row.email_id is not None) == (MessageStatus.SENT, 1, True)
    ticket = _ok(web.get(f"/api/tickets/TKT-{1:06d}"))
    assert [(m["channel"], m["status"]) for m in ticket["channel_messages"]] == [
        ("whatsapp", "sent")
    ]
    with db_session(migrated_db) as db:
        for sql, message in (
            ("UPDATE channel_messages SET body_text = 'x'", "never changed"),
            ("DELETE FROM channel_messages", "never deleted"),
        ):
            with pytest.raises(DBAPIError, match=message):
                db.execute(text(sql))
            db.rollback()


@pytest.mark.requires_db
def test_an_opted_out_channel_is_never_used(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    cid = client_with(web, admin, whatsapp=False, sms=False)
    cycle = first_cycle(migrated_db)
    phone = RecordingProvider()
    run(factory, settings_for(migrated_db), now=at(cycle.alert_date), messenger=phone)
    assert (alert_emails(migrated_db), phone.sent, messages(migrated_db)) == (1, [], [])

    # Agreed, queued, then withdrawn before the job sends it: it is skipped, not sent.
    client = _ok(web.get(f"/api/clients/{cid}"))
    contact = client["contacts"][0]["id"]
    _ok(
        web.put(
            f"/api/clients/{cid}/contacts/{contact}/consent",
            json={"channel": "whatsapp", "granted": True, "note": "Asked on the phone"},
            headers=admin.headers,
        )
    )
    settings = settings_for(migrated_db)
    with db_session(migrated_db) as db:
        row = db.get(Contact, contact)
        client_row = db.scalar(select(Client).where(Client.client_id == cid))
        email_id = db.scalar(select(Email.id).where(Email.kind == EmailKind.CLIENT_ALERT))
        assert row is not None
        assert client_row is not None
        assert email_id is not None
        queued = queue_for_alert(
            db,
            settings,
            client=client_row,
            contact=row,
            cycle_id=cycle.id,
            email_id=email_id,
            template="reminder",
            values={
                "firm_name": "F",
                "client_name": "Asha Rao",
                "filing": "GSTR-1",
                "period": "Apr 2026",
                "due_date": cycle.due_date,
                "ticket_id": "TKT-000001",
            },
        )
        assert queued is not None
        db.commit()
    _ok(
        web.put(
            f"/api/clients/{cid}/contacts/{contact}/consent",
            json={"channel": "whatsapp", "granted": False, "note": "Stop WhatsApp"},
            headers=admin.headers,
        )
    )
    assert dispatch(factory, settings, phone).skipped == 1
    assert phone.sent == []
    assert [m.status for m in messages(migrated_db)] == [MessageStatus.SKIPPED]


@pytest.mark.requires_db
def test_a_failure_falls_back_to_the_email_and_tells_the_owner(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    client_with(web, admin, whatsapp=False, sms=True)
    cycle = first_cycle(migrated_db)
    run(
        factory,
        settings_for(migrated_db),
        now=at(cycle.alert_date),
        messenger=RecordingProvider(fail_with=PermanentFailure("number not on SMS")),
    )
    [row] = messages(migrated_db)
    assert (row.channel, row.status, row.last_error) == (
        Channel.SMS,
        MessageStatus.FAILED,
        "number not on SMS",
    )
    assert alert_emails(migrated_db) == 1  # the client still has the email
    with db_session(migrated_db) as db:
        told = list(
            db.scalars(
                select(Notification).where(Notification.kind == NotificationKind.CHANNEL_FAILED)
            )
        )
    assert [n.user_id for n in told] == [admin.user_id]
    assert "The email went as usual" in told[0].body


@pytest.mark.requires_db
def test_a_temporary_failure_is_tried_three_times(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    client_with(web, admin, whatsapp=True, sms=False)
    cycle = first_cycle(migrated_db)
    down = RecordingProvider(fail_with=TemporaryFailure("provider busy"))
    for hour in (10, 11, 12):
        run(factory, settings_for(migrated_db), now=at(cycle.alert_date, hour), messenger=down)
    [row] = messages(migrated_db)
    assert (row.status, row.attempts) == (MessageStatus.FAILED, 3)
    up = RecordingProvider()
    run(factory, settings_for(migrated_db), now=at(cycle.alert_date, 13), messenger=up)
    assert up.sent == []  # a failed message is final


@pytest.mark.requires_db
def test_nothing_goes_on_the_second_channel_while_it_is_switched_off_or_unapproved(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    client_with(web, admin, whatsapp=True, sms=True)
    cycle = first_cycle(migrated_db)
    phone = RecordingProvider()
    run(factory, make_settings(database_url=migrated_db), now=at(cycle.alert_date), messenger=phone)
    unapproved = settings_for(migrated_db, messaging_approved_templates="first_alert")
    run(factory, unapproved, now=at(cycle.alert_date + timedelta(days=2)), messenger=phone)
    assert (phone.sent, messages(migrated_db)) == ([], [])
    assert alert_emails(migrated_db) == 2
