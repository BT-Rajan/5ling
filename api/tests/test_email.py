"""Chunk C13: email service and outbox (BRD INT-01, FR-AL-06, FR-AU-03, CR-03)."""

from __future__ import annotations

import hashlib
import hmac
import json
import socketserver
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from email import message_from_bytes
from typing import Any

import pytest
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.mail.outbox import alert_admins_audit_failed
from app.mail.provider import (
    Outgoing,
    PermanentFailure,
    RecordingProvider,
    SmtpProvider,
    TemporaryFailure,
    build_message,
)
from app.mail.templates import TEMPLATES, Template, TemplateError, check_template, render
from app.mail.worker import retry_delay, send_due
from app.main import create_app
from app.models import CheckTrigger, Email, EmailEvent, EmailKind, EmailStatus, Role
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import events
from tests.conftest import make_settings
from tests.helpers import Signed, db_session, make_user, sign_in
from tests.test_audit_verify import _check, _write, tampering

HOOK_SECRET = "hook-" + "0123456789abcdefghijklmnopqrstuv"


# --- Templates (LL-056) ------------------------------------------------------------------------


def test_a_template_fills_every_blank_from_plain_values() -> None:
    out = render(
        "stage_change",
        {
            "firm_name": "Rao & Co",
            "client_name": "Asha Rao",
            "client_id": 100001,
            "ticket_id": "TKT-000012",
            "filing": "GSTR-1",
            "period": "Apr 2026",
            "stage": "In review",
            "previous_stage": "Preparing",
            "due_date": date(2026, 5, 11),
            "note": None,
            "sender_name": "Bina Shah",
        },
    )
    assert out.subject == "GSTR-1 Apr 2026: In review (TKT-000012)"
    assert "Due date: Mon 11 May 2026." in out.body
    assert "client ID 100001" in out.body
    assert "\n\n\n" not in out.body  # an empty note leaves no gap


def test_templates_refuse_missing_or_unknown_blanks_and_keep_the_subject_to_one_line() -> None:
    with pytest.raises(TemplateError, match="needs"):
        render("test", {"firm_name": "X"})
    with pytest.raises(TemplateError, match="unknown blanks"):
        check_template(Template("bad", "{{password}}", "hi"))
    with pytest.raises(TemplateError, match="no template"):
        render("nope", {})
    out = render("test", {"firm_name": "Evil\r\nBcc: victim@x.in", "sender_name": "{{firm_name}}"})
    assert "\n" not in out.subject
    assert "\r" not in out.subject
    assert "{{firm_name}}" in out.body  # a value is never filled in again as a template
    assert all(t.blanks() for t in TEMPLATES.values())


def test_message_headers_cannot_be_injected() -> None:
    message = Outgoing("a" * 32, ["x@example.in"], [], "Hello\nBcc: victim@example.in", "Body")
    with pytest.raises(ValueError, match=r"linefeed|carriage"):
        build_message(message, from_address="no-reply@firm.in", from_name="Firm")
    ok, message_id = build_message(
        Outgoing("b" * 32, ["x@example.in"], ["y@example.in"], "Hello", "Body"),
        from_address="no-reply@firm.in",
        from_name="Rao, Sons & Co",
    )
    assert message_id.endswith(f".{'b' * 32}@firm.in>")
    assert ok["From"] == '"Rao, Sons & Co" <no-reply@firm.in>'
    assert ok["Cc"] == "y@example.in"


def test_retries_back_off_then_settle_hourly() -> None:
    assert [retry_delay(n).total_seconds() / 60 for n in range(1, 8)] == [1, 5, 15, 30, 60, 60, 60]


# --- Settings ----------------------------------------------------------------------------------


def test_production_needs_encrypted_smtp_and_a_real_sender() -> None:
    real = {"env": "production", "allowed_hosts": "app.firm.in", "allowed_origins": ""}
    with pytest.raises(ValidationError, match="smtp_security"):
        make_settings(**real, smtp_host="smtp.provider.in", email_from_address="hi@firm.in")
    with pytest.raises(ValidationError, match="real email_from_address"):
        make_settings(
            **real,
            smtp_security="tls",
            smtp_host="smtp.provider.in",
            email_from_address="no-reply@ledgerline.example.com",
        )
    ok = make_settings(**real, smtp_security="starttls", email_from_address="hi@firm.in")
    assert ok.smtp_security == "starttls"
    # A relay on the same machine may take plain SMTP.
    assert make_settings(**real, email_from_address="hi@firm.in").smtp_host == "127.0.0.1"
    with pytest.raises(ValidationError, match="at least"):
        make_settings(email_webhook_secret="short")
    with pytest.raises(ValidationError, match="valid address"):
        make_settings(email_from_address="not an address")


# --- The SMTP provider, against a tiny local SMTP server ---------------------------------------


@dataclass
class FakeSmtp:
    rcpt_reply: str = "250 OK"
    received: list[bytes] = field(default_factory=list)


def _handler(state: FakeSmtp) -> type[socketserver.StreamRequestHandler]:
    class Handler(socketserver.StreamRequestHandler):
        def reply(self, line: str) -> None:
            self.wfile.write(line.encode() + b"\r\n")

        def handle(self) -> None:
            self.reply("220 fake ESMTP")
            while line := self.rfile.readline():
                command = line.decode().strip().upper()
                if command.startswith(("EHLO", "HELO")):
                    self.reply("250 fake")
                elif command.startswith("MAIL FROM"):
                    self.reply("250 OK")
                elif command.startswith("RCPT TO"):
                    self.reply(state.rcpt_reply)
                elif command == "DATA":
                    self.reply("354 go on")
                    data = b""
                    while (chunk := self.rfile.readline()) not in (b".\r\n", b""):
                        data += chunk
                    state.received.append(data)
                    self.reply("250 queued as FAKE1")
                elif command == "QUIT":
                    self.reply("221 bye")
                    return
                else:
                    self.reply("250 OK")

    return Handler


@pytest.fixture
def smtp() -> Iterator[tuple[FakeSmtp, int]]:
    state = FakeSmtp()
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _handler(state))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield state, server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _provider(port: int) -> SmtpProvider:
    return SmtpProvider(
        host="127.0.0.1",
        port=port,
        security="none",
        username=None,
        password=None,
        timeout=5,
        from_address="no-reply@firm.in",
        from_name="Firm",
    )


def test_smtp_sends_and_sorts_refusals_into_permanent_and_temporary(
    smtp: tuple[FakeSmtp, int],
) -> None:
    state, port = smtp
    message = Outgoing("c" * 32, ["asha@example.in"], [], "Hello", "Namaste\n")
    message_id = _provider(port).send(message)
    parsed = message_from_bytes(state.received[0])
    assert parsed["Message-ID"] == message_id
    assert parsed["To"] == "asha@example.in"
    assert parsed["Auto-Submitted"] == "auto-generated"

    state.rcpt_reply = "550 no such user"
    with pytest.raises(PermanentFailure):
        _provider(port).send(message)
    state.rcpt_reply = "451 try later"
    with pytest.raises(TemporaryFailure):
        _provider(port).send(message)
    with pytest.raises(TemporaryFailure, match="could not reach"):
        _provider(1).send(message)  # nothing listens on port 1


# --- With a database ---------------------------------------------------------------------------


@pytest.fixture
def factory(migrated_db: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(migrated_db)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def hooked(migrated_db: str) -> Iterator[TestClient]:
    """The app with delivery reports switched on."""
    settings: Settings = make_settings(database_url=migrated_db, email_webhook_secret=HOOK_SECRET)
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def boss(hooked: TestClient, migrated_db: str) -> Signed:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR, name="Boss Person")
    return sign_in(hooked, "boss@firm.in")


def report(
    client: TestClient,
    events_: list[dict[str, Any]],
    *,
    secret: str = HOOK_SECRET,
    stamp: int | None = None,
) -> Any:
    body = json.dumps({"events": events_}).encode()
    ts = str(int(time.time()) if stamp is None else stamp)
    sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/email/events",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Ledgerline-Timestamp": ts,
            "X-Ledgerline-Signature": f"sha256={sig}",
        },
    )


def history(url: str, email_id: int) -> list[tuple[str, str]]:
    with db_session(url) as db:
        return [
            (e.status.value, e.source.value)
            for e in db.scalars(
                select(EmailEvent).where(EmailEvent.email_id == email_id).order_by(EmailEvent.id)
            )
        ]


# --- Done when: a test email moves queued to sent to delivered; a forced failure shows failed ---


@pytest.mark.requires_db
def test_a_test_email_moves_from_queued_to_sent_to_delivered(
    hooked: TestClient, boss: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    made = hooked.post("/api/admin/emails/test", json={}, headers=boss.headers)
    assert made.status_code == 201, made.text
    email = made.json()
    assert (email["status"], email["to"], email["kind"]) == ("queued", ["boss@firm.in"], "test")
    assert email["subject"] == "Test email from Ledgerline"
    assert "sent by Boss Person" in email["body"]
    assert "email.test_queued" in events(migrated_db)

    provider = RecordingProvider()
    tally = send_due(factory, provider)
    assert (tally.sent, tally.failed) == (1, 0)
    assert provider.sent[0].to == ["boss@firm.in"]
    sent = hooked.get(f"/api/admin/emails/{email['id']}").json()
    assert sent["status"] == "sent"
    assert sent["sent_at"] is not None
    assert send_due(factory, provider).handled == 0  # sent once only

    with db_session(migrated_db) as db:
        public_id = db.scalar(select(Email.public_id).where(Email.id == email["id"]))
    message_id = f"<1.2.3.{public_id}@firm.in>"
    done = report(hooked, [{"message_id": message_id, "event": "delivered", "detail": "250 ok"}])
    assert done.json() == {"applied": 1, "ignored": 0}
    delivered = hooked.get(f"/api/admin/emails/{email['id']}").json()
    assert delivered["status"] == "delivered"
    assert [(e["status"], e["source"]) for e in delivered["events"]] == [
        ("draft", "app"),
        ("queued", "app"),
        ("sent", "worker"),
        ("delivered", "provider"),
    ]
    # The same report again, or a stale "failed" after delivery, changes nothing.
    assert report(hooked, [{"email_id": public_id, "event": "delivered"}]).json()["ignored"] == 1
    assert report(hooked, [{"email_id": public_id, "event": "failed"}]).json()["ignored"] == 1
    # A later bounce still counts.
    assert report(hooked, [{"email_id": public_id, "event": "bounced"}]).json()["applied"] == 1
    listed = hooked.get("/api/admin/emails", params={"status": "bounced"}).json()
    assert [r["id"] for r in listed["items"]] == [email["id"]]


@pytest.mark.requires_db
def test_a_refused_email_fails_at_once(
    hooked: TestClient, boss: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    made = hooked.post(
        "/api/admin/emails/test", json={"to": "nobody@example.in"}, headers=boss.headers
    ).json()
    tally = send_due(
        factory, RecordingProvider(fail_with=PermanentFailure("the mail server replied 550"))
    )
    assert (tally.sent, tally.failed) == (0, 1)
    failed = hooked.get(f"/api/admin/emails/{made['id']}").json()
    assert failed["status"] == "failed"
    assert failed["last_error"] == "the mail server replied 550"
    assert failed["events"][-1]["detail"] == "refused: the mail server replied 550"
    assert hooked.get("/api/admin/emails", params={"status": "failed"}).json()["total"] == 1


@pytest.mark.requires_db
def test_temporary_failures_are_retried_for_24_hours_then_fail(
    hooked: TestClient, boss: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    made = hooked.post("/api/admin/emails/test", json={}, headers=boss.headers).json()
    flaky = RecordingProvider(fail_with=TemporaryFailure("could not reach the mail server"))
    with db_session(migrated_db) as db:
        email = db.get(Email, made["id"])
        assert email is not None
        queued_at = email.queued_at
    assert queued_at is not None

    first = send_due(factory, flaky, now=queued_at)
    assert (first.retrying, first.failed) == (1, 0)
    assert send_due(factory, flaky, now=queued_at + timedelta(seconds=30)).handled == 0  # waits
    second = send_due(factory, flaky, now=queued_at + timedelta(minutes=2))
    assert second.retrying == 1
    with db_session(migrated_db) as db:
        email = db.get(Email, made["id"])
        assert email is not None
        assert email.attempts == 2
        assert email.status is EmailStatus.QUEUED

    late = send_due(factory, flaky, now=queued_at + timedelta(hours=25))
    assert late.failed == 1
    gone = hooked.get(f"/api/admin/emails/{made['id']}").json()
    assert gone["status"] == "failed"
    assert "not sent within 24 hours" in gone["events"][-1]["detail"]

    # It recovers once the server is back, for anything still inside its 24 hours.
    again = hooked.post("/api/admin/emails/test", json={}, headers=boss.headers).json()
    assert send_due(factory, RecordingProvider()).sent == 1
    assert hooked.get(f"/api/admin/emails/{again['id']}").json()["status"] == "sent"


@pytest.mark.requires_db
def test_a_claimed_email_is_not_claimed_twice(
    hooked: TestClient, boss: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    from app.mail.worker import claim

    hooked.post("/api/admin/emails/test", json={}, headers=boss.headers)
    with db_session(migrated_db) as db:
        now = db.scalar(select(Email.next_attempt_at))
    assert now is not None
    assert len(claim(factory, now, 10)) == 1
    assert claim(factory, now, 10) == []  # leased for 10 minutes
    assert len(claim(factory, now + timedelta(minutes=11), 10)) == 1  # the worker died: retry


# --- Delivery reports ------------------------------------------------------------------------


@pytest.mark.requires_db
def test_delivery_reports_must_be_signed_and_fresh(
    hooked: TestClient, boss: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    made = hooked.post("/api/admin/emails/test", json={}, headers=boss.headers).json()
    send_due(factory, RecordingProvider())
    with db_session(migrated_db) as db:
        public_id = db.scalar(select(Email.public_id).where(Email.id == made["id"]))
    good = [{"email_id": public_id, "event": "delivered"}]

    assert report(hooked, good, secret="x" * 40).status_code == 401
    assert report(hooked, good, stamp=int(time.time()) - 3600).status_code == 401
    unsigned = hooked.post("/api/email/events", json={"events": good})
    assert unsigned.status_code == 401
    assert hooked.get(f"/api/admin/emails/{made['id']}").json()["status"] == "sent"
    assert report(hooked, [{"email_id": "f" * 32, "event": "delivered"}]).json() == {
        "applied": 0,
        "ignored": 1,
    }
    assert report(hooked, [{"email_id": "not-ours", "event": "delivered"}]).status_code == 422
    assert report(hooked, [{"email_id": public_id, "event": "opened"}]).status_code == 422


@pytest.mark.requires_db
def test_delivery_reports_are_off_without_a_secret(web: TestClient) -> None:
    assert web.post("/api/email/events", json={"events": []}).status_code == 404


@pytest.mark.requires_db
def test_a_report_that_beats_the_worker_still_counts(
    hooked: TestClient, boss: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    made = hooked.post("/api/admin/emails/test", json={}, headers=boss.headers).json()
    with db_session(migrated_db) as db:
        public_id = db.scalar(select(Email.public_id).where(Email.id == made["id"]))
    assert report(hooked, [{"email_id": public_id, "event": "delivered"}]).json()["applied"] == 1
    assert hooked.get(f"/api/admin/emails/{made['id']}").json()["status"] == "delivered"
    assert send_due(factory, RecordingProvider()).handled == 0  # nothing left to send


# --- The record cannot be rewritten ----------------------------------------------------------


@pytest.mark.requires_db
def test_a_queued_email_and_its_history_cannot_be_changed(
    hooked: TestClient, boss: Signed, migrated_db: str
) -> None:
    hooked.post("/api/admin/emails/test", json={}, headers=boss.headers)
    engine = create_engine(migrated_db)
    try:
        for sql, message in (
            ("UPDATE emails SET subject = 'Changed'", "cannot be changed"),
            ("UPDATE emails SET to_addresses = JSON_ARRAY('x@evil.in')", "cannot be changed"),
            ("UPDATE emails SET status = 'draft'", "cannot move"),
            ("UPDATE emails SET status = 'delivered'", "cannot move"),
            ("DELETE FROM emails", "never deleted"),
            ("UPDATE email_events SET detail = 'x'", "append-only"),
            ("DELETE FROM email_events", "append-only"),
        ):
            with pytest.raises(Exception, match=message), engine.begin() as conn:
                conn.execute(text(sql))
    finally:
        engine.dispose()


# --- FR-AU-03: a failed audit check emails every Administrator --------------------------------


@pytest.mark.requires_db
def test_a_failed_audit_check_emails_every_administrator_once_a_day(migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    make_user(migrated_db, "deputy@firm.in", Role.ADMINISTRATOR)
    make_user(migrated_db, "staff@firm.in", Role.STAFF)
    _write(migrated_db, 3)
    assert _check(migrated_db).ok
    with db_session(migrated_db) as db:
        assert db.scalar(select(Email.id)) is None  # a passing check sends nothing
    with tampering(migrated_db) as conn:
        conn.execute(text("UPDATE audit_entries SET actor_user_id = 8 WHERE seq = 1"))

    assert not _check(migrated_db, CheckTrigger.SCHEDULED).ok
    assert not _check(migrated_db, CheckTrigger.SCHEDULED).ok
    with db_session(migrated_db) as db:
        alerts = list(db.scalars(select(Email).where(Email.kind == EmailKind.AUDIT_ALERT)))
    assert len(alerts) == 1
    assert alerts[0].status is EmailStatus.QUEUED
    assert alerts[0].to_addresses == ["boss@firm.in", "deputy@firm.in"]
    assert "audit log check failed" in alerts[0].subject
    assert "at entry 1" in alerts[0].body_text


@pytest.mark.requires_db
def test_no_alert_without_an_administrator(migrated_db: str) -> None:
    with db_session(migrated_db) as db:
        assert alert_admins_audit_failed(db, "x", "Firm") is None


@pytest.mark.requires_db
def test_a_bad_test_address_is_refused(hooked: TestClient, boss: Signed) -> None:
    bad = hooked.post("/api/admin/emails/test", json={"to": "not-an-email"}, headers=boss.headers)
    assert bad.status_code == 422


@pytest.mark.requires_db
def test_the_worker_runs_from_the_command_line(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import cli
    from app.config import get_settings

    from tests.conftest import GOOD_SECRET

    monkeypatch.setenv("LL_DATABASE_URL", migrated_db)
    monkeypatch.setenv("LL_SECRET_KEY", GOOD_SECRET)
    monkeypatch.setenv("LL_SMTP_PORT", "1")  # nothing listens: the email waits for a retry
    get_settings.cache_clear()
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    with db_session(migrated_db) as db:
        alert_admins_audit_failed(db, "x", "Firm")
        db.commit()
    try:
        assert cli.main(["send-emails", "--once"]) == 0
    finally:
        get_settings.cache_clear()
    with db_session(migrated_db) as db:
        email = db.scalar(select(Email))
        assert email is not None
        assert (email.status, email.attempts) == (EmailStatus.QUEUED, 1)
    assert cli.main(["send-emails", "--interval", "0"]) == 2
