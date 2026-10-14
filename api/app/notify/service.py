"""Internal alerts (BRD FR-IN-01 to FR-IN-03).

- Documents received (FR-IN-01): when the last required checklist item passes its virus check,
  the cycle moves on to Documents received (through Awaiting documents if needed, so every
  stage change is recorded and drafts its client update) and the owner is notified, in the same
  transaction as the upload or scan. "Within 1 minute" holds because nothing waits for a job.
- Waiting on client (FR-IN-02): `grace` days after the first alert, a cycle still missing items
  notifies its owner, listing exactly the items not yet received.
- Escalation (FR-IN-03): `days_before` the due date, a cycle that has received nothing at all
  is escalated to the Administrators, once; dismissing it needs a note.

Each notification has a unique key, so every check can run as often as it likes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.cycles.service import ticket_code, transition
from app.files.checklist import ItemView, items_for
from app.mail.updates import draft_updates
from app.models import (
    AlertKind,
    Client,
    ClientStatus,
    Cycle,
    CycleAlert,
    Filing,
    Notification,
    NotificationKind,
    Role,
    Stage,
    Ticket,
)
from app.timeutil import utcnow

log = logging.getLogger("ledgerline.jobs")

COLLECTING = (Stage.SCHEDULED, Stage.AWAITING_DOCUMENTS)


def notify(
    db: Session,
    *,
    kind: NotificationKind,
    dedupe_key: str,
    client_pk: int,
    cycle_id: int | None,
    title: str,
    body: str,
    user_id: int | None = None,
    for_role: Role | None = None,
) -> Notification | None:
    """Add a notification unless one with this key exists. None if it already did."""
    if (user_id is None) == (for_role is None):
        raise ValueError("a notification is for one person or for one role")
    if db.scalar(select(Notification.id).where(Notification.dedupe_key == dedupe_key)):
        return None
    row = Notification(
        kind=kind,
        user_id=user_id,
        for_role=for_role,
        client_pk=client_pk,
        cycle_id=cycle_id,
        title=title[:200],
        body=body[:2000],
        dedupe_key=dedupe_key,
        created_at=utcnow(),
    )
    try:
        with db.begin_nested():  # a race on the key loses quietly, keeping the outer work
            db.add(row)
    except IntegrityError:
        return None
    return row


def _about(db: Session, cycle: Cycle) -> tuple[Client, Ticket, Filing]:
    client = db.get(Client, cycle.client_pk)
    ticket = db.get(Ticket, cycle.ticket_id)
    filing = db.get(Filing, cycle.filing_id)
    if client is None or ticket is None or filing is None:  # foreign keys prevent it
        raise RuntimeError("cycle refers to missing rows")
    return client, ticket, filing


def _missing_lines(views: list[ItemView]) -> str:
    return "\n".join(
        f"- {v.item.label}" + ("" if v.item.required else " (optional)")
        for v in views
        if v.status != "received"
    )


def documents_arrived(db: Session, cycle_id: int, *, firm_name: str) -> bool:
    """FR-IN-01: if every required item is now in, move the cycle on and tell its owner.

    Call after an upload or scan, before committing. Returns True if it moved the cycle.
    """
    cycle = db.scalar(
        select(Cycle)
        .where(Cycle.id == cycle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if cycle is None or cycle.stage not in COLLECTING:
        return False
    views = items_for(db, cycle.id)
    required = [v for v in views if v.item.required]
    if not required or any(v.status != "received" for v in required):
        return False
    client, ticket, filing = _about(db, cycle)
    if cycle.stage is Stage.SCHEDULED:
        transition(db, cycle, ticket, "request_documents", actor_id=None, actor_role=None)
    transition(db, cycle, ticket, "documents_received", actor_id=None, actor_role=None)
    notify(
        db,
        kind=NotificationKind.DOCUMENTS_RECEIVED,
        dedupe_key=f"received:{cycle.id}",
        client_pk=client.id,
        cycle_id=cycle.id,
        user_id=client.owner_id,
        title=f"Documents received: {client.legal_name}, {filing.code} {cycle.period_label}",
        body=f"Every required document for {ticket_code(ticket.number)} has arrived and passed "
        "the virus check. The filing is now in Documents received.",
    )
    draft_updates(db, firm_name, client_pk=client.id)  # CR-03: the moves are state changes
    return True


@dataclass
class Tally:
    waiting: int = 0
    escalated: int = 0


def check_cycles(
    factory: sessionmaker[Session], *, today: date, grace_days: int, days_before: int
) -> Tally:
    """FR-IN-02 and FR-IN-03 for every active client's cycles still collecting documents."""
    tally = Tally()
    with factory() as db:
        candidates = list(
            db.execute(
                select(Cycle.id, Cycle.due_date)
                .join(Client, Client.id == Cycle.client_pk)
                .where(Client.status == ClientStatus.ACTIVE, Cycle.stage.in_(COLLECTING))
                .order_by(Cycle.due_date, Cycle.id)
            )
        )
        firsts = dict(
            db.execute(
                select(CycleAlert.cycle_id, CycleAlert.sent_on).where(
                    CycleAlert.kind == AlertKind.FIRST,
                    CycleAlert.cycle_id.in_([c for c, _ in candidates] or [0]),
                )
            ).all()
        )
    for cycle_id, due in candidates:
        first = firsts.get(cycle_id)
        waiting = first is not None and today >= first + timedelta(days=grace_days)
        escalate = today >= due - timedelta(days=days_before)
        if not (waiting or escalate):
            continue
        with factory() as db:
            cycle = db.get(Cycle, cycle_id)
            if cycle is None:
                continue
            views = items_for(db, cycle_id)
            required = [v for v in views if v.item.required]
            if not required or all(v.status == "received" for v in required):
                continue
            client, ticket, filing = _about(db, cycle)
            code = ticket_code(ticket.number)
            what = f"{client.legal_name}, {filing.code} {cycle.period_label}"
            if waiting and notify(
                db,
                kind=NotificationKind.WAITING_ON_CLIENT,
                dedupe_key=f"waiting:{cycle_id}",
                client_pk=client.id,
                cycle_id=cycle_id,
                user_id=client.owner_id,
                title=f"Waiting on client: {what}",
                body=f"{code} is due on {cycle.due_date:%d %b %Y}. Still missing:\n"
                + _missing_lines(views),
            ):
                tally.waiting += 1
            if (
                escalate
                and not any(v.status == "received" for v in views)
                and notify(
                    db,
                    kind=NotificationKind.ESCALATION,
                    dedupe_key=f"escalation:{cycle_id}",
                    client_pk=client.id,
                    cycle_id=cycle_id,
                    for_role=Role.ADMINISTRATOR,
                    title=f"Escalation: nothing received for {what}",
                    body=f"{code} is due on {cycle.due_date:%d %b %Y} and no document has "
                    "arrived. Missing:\n" + _missing_lines(views),
                )
            ):
                tally.escalated += 1
                audit(db, "cycle.escalated", target=("ticket", code), details={"due": str(due)})
            db.commit()
    if tally.waiting or tally.escalated:
        log.info(
            "internal alerts",
            extra={"ctx": {"waiting": tally.waiting, "escalated": tally.escalated}},
        )
    return tally
