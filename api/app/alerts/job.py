"""Automatic client alerts and reminders (BRD FR-AL-01, FR-AL-03).

Every 15 minutes (deploy/systemd/ledgerline-alerts.timer), within the sending hours, for each
cycle of an active client that is still collecting documents (Scheduled or Awaiting documents)
and still missing a required document:

- on or after its alert date, with no alert yet: the first alert;
- then a reminder every `reminder_interval_days` (default 2), and daily once the due date has
  passed.

Each alert carries a new private link (which replaces any earlier unopened one), the due date
and what is still needed, and goes to the primary contact. Completeness is checked at the
moment of sending, so reminders stop at the next run after the checklist is complete (within
15 minutes; the BRD allows an hour).

Safe to run twice or at once: one run at a time holds a MySQL named lock, each cycle is handled
in its own transaction under a row lock, and at most one alert per cycle per day is enforced by
a unique key. Alerts are never queued outside the sending hours.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.config import Settings
from app.cycles.service import ticket_code
from app.files.checklist import items_for
from app.mail.outbox import create
from app.mail.templates import render
from app.models import (
    AlertKind,
    Client,
    ClientStatus,
    Contact,
    Cycle,
    CycleAlert,
    EmailKind,
    Filing,
    LinkOrigin,
    Stage,
    Ticket,
)
from app.notify.service import check_cycles
from app.portal.links import issue, url_for
from app.timeutil import IST, ensure_utc, format_ist, utcnow

log = logging.getLogger("ledgerline.jobs")

COLLECTING = (Stage.SCHEDULED, Stage.AWAITING_DOCUMENTS)
_LOCK = "ledgerline.client_alerts"


def due_kind(
    *, alert_date: date, due_date: date, last_sent: date | None, today: date, interval_days: int
) -> AlertKind | None:
    """Which alert, if any, a cycle is owed today."""
    if today < alert_date:
        return None
    if last_sent is None:
        return AlertKind.FIRST
    if last_sent >= today:
        return None  # one a day at most
    if today > due_date or (today - last_sent).days >= interval_days:
        return AlertKind.REMINDER
    return None


@dataclass
class Tally:
    checked: int = 0
    sent: int = 0
    failed: int = 0
    waiting: int = 0  # owners told a cycle is waiting on the client (FR-IN-02)
    escalated: int = 0  # cycles escalated to the Administrators (FR-IN-03)
    skipped_reason: str | None = None  # outside sending hours, or another run in progress
    failures: list[str] = field(default_factory=list)


def _alert_one(db: Session, settings: Settings, cycle_id: int, today: date) -> CycleAlert | None:
    cycle = db.scalar(
        select(Cycle)
        .where(Cycle.id == cycle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if cycle is None or cycle.stage not in COLLECTING:
        return None
    client = db.get(Client, cycle.client_pk)
    if client is None or client.status is not ClientStatus.ACTIVE:
        return None
    last = db.scalar(
        select(CycleAlert.sent_on)
        .where(CycleAlert.cycle_id == cycle.id)
        .order_by(CycleAlert.sent_on.desc())
        .limit(1)
    )
    kind = due_kind(
        alert_date=cycle.alert_date,
        due_date=cycle.due_date,
        last_sent=last,
        today=today,
        interval_days=settings.reminder_interval_days,
    )
    if kind is None:
        return None
    missing = [v for v in items_for(db, cycle.id) if v.status != "received"]
    if not any(v.item.required for v in missing):
        return None  # complete (or nothing asked for): no alert, and reminders stop
    contact = db.scalar(
        select(Contact).where(Contact.client_pk == client.id, Contact.is_primary.is_(True))
    )
    if contact is None:
        raise RuntimeError("no primary contact")
    ticket = db.get(Ticket, cycle.ticket_id)
    filing = db.get(Filing, cycle.filing_id)
    if ticket is None or filing is None:  # foreign keys prevent it
        raise RuntimeError("cycle refers to missing rows")

    token, link = issue(
        db, settings, client=client, cycle=cycle, origin=LinkOrigin.SYSTEM, created_by_id=None
    )
    lines = "\n".join(
        f"- {v.item.label}" + ("" if v.item.required else " (if you have it)") for v in missing
    )
    values: dict[str, str | int | date | None] = {
        "firm_name": settings.firm_name,
        "client_name": client.legal_name,
        "client_id": client.client_id,
        "ticket_id": ticket_code(ticket.number),
        "filing": filing.code,
        "period": cycle.period_label,
        "due_date": cycle.due_date,
        "detail": lines,
        "link": url_for(settings, token),
        "expires": format_ist(ensure_utc(link.expires_at), "%a %d %b %Y, %H:%M IST"),
    }
    if kind is AlertKind.REMINDER:
        values["note"] = (
            "The due date has passed. Please upload them as soon as you can."
            if today > cycle.due_date
            else None
        )
    email = create(
        db,
        kind=EmailKind.CLIENT_ALERT,
        to=[contact.email],
        content=render("first_alert" if kind is AlertKind.FIRST else "reminder", values),
        client_pk=client.id,
        ticket_id=ticket.id,
        queue_now=True,
    )
    alert = CycleAlert(
        cycle_id=cycle.id,
        kind=kind,
        sent_on=today,
        email_id=email.id,
        portal_link_id=link.id,
        created_at=utcnow(),
    )
    db.add(alert)
    audit(
        db,
        "alert.sent",
        target=("ticket", ticket_code(ticket.number)),
        details={
            "kind": kind,
            "client_id": client.client_id,
            "missing": len(missing),
            "email": email.id,
        },
    )
    return alert


def run(
    factory: sessionmaker[Session], settings: Settings, *, now: datetime | None = None
) -> Tally:
    """One pass over every cycle that may be owed an alert today."""
    now_ist = ensure_utc(now or utcnow()).astimezone(IST)
    today = now_ist.date()
    tally = Tally()
    # Internal alerts are not limited to the client sending hours (FR-IN-02, FR-IN-03).
    internal = check_cycles(
        factory,
        today=today,
        grace_days=settings.waiting_grace_days,
        days_before=settings.escalation_days_before,
    )
    tally.waiting, tally.escalated = internal.waiting, internal.escalated
    if not settings.alert_hours_start <= now_ist.hour < settings.alert_hours_end:
        tally.skipped_reason = "outside sending hours"
        return tally
    with factory() as lock_db:
        if lock_db.scalar(text("SELECT GET_LOCK(:name, 0)"), {"name": _LOCK}) != 1:
            tally.skipped_reason = "another run is in progress"
            return tally
        try:
            with factory() as db:
                ids = list(
                    db.scalars(
                        select(Cycle.id)
                        .join(Client, Client.id == Cycle.client_pk)
                        .where(
                            Client.status == ClientStatus.ACTIVE,
                            Cycle.stage.in_(COLLECTING),
                            Cycle.alert_date <= today,
                        )
                        .order_by(Cycle.due_date, Cycle.id)
                    )
                )
            for cycle_id in ids:
                tally.checked += 1
                with factory() as db:
                    try:
                        if _alert_one(db, settings, cycle_id, today) is not None:
                            db.commit()
                            tally.sent += 1
                        else:
                            db.rollback()
                    except IntegrityError:
                        db.rollback()  # another run sent today's alert a moment ago
                    except Exception as exc:
                        db.rollback()
                        tally.failed += 1
                        tally.failures.append(f"cycle {cycle_id}: {type(exc).__name__}")
                        log.exception("client alert failed", extra={"ctx": {"cycle": cycle_id}})
        finally:
            lock_db.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK})
    facts = {"checked": tally.checked, "sent": tally.sent, "failed": tally.failed}
    if tally.failed:
        log.critical("CLIENT ALERTS FAILED", extra={"ctx": facts})
    else:
        log.info("client alerts ran", extra={"ctx": facts})
    return tally
