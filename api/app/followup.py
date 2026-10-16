"""Nil filings (FR-CY-03) and phone follow-up (FR-IN-05), chunk C34.

`confirm_no_activity` is the client's "nothing this period" from the portal. It is stored as
given, the cycle moves straight to In review (the system's move, so no one is its preparer and the
owner or an Administrator may approve it), and the owner is told.

`record_phone_contact` keeps a staff member's call note with the date the client promised.
`phone_promises` (run with the alerts job, every 15 minutes) reminds that person on the promised
date if the work is still waiting on the client; if it has moved on, the promise is settled
without a reminder. Each promise is dealt with once (`reminded_at`, set once; trigger 0029).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.cycles.service import ticket_code, transition
from app.mail.updates import draft_updates
from app.models import (
    Client,
    ClientStatus,
    Cycle,
    Filing,
    NilConfirmation,
    NotificationKind,
    PhoneContact,
    PortalLink,
    Role,
    Stage,
    Task,
    TaskStatus,
    Ticket,
    User,
)
from app.notify.service import COLLECTING, notify
from app.timeutil import utcnow

log = logging.getLogger("ledgerline.jobs")


class FollowUpRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def confirm_no_activity(
    db: Session, link: PortalLink, cycle_id: int, note: str | None, *, firm_name: str
) -> NilConfirmation:
    """Store the client's confirmation and move the cycle to In review. Caller commits."""
    cycle = db.scalar(
        select(Cycle)
        .where(Cycle.id == cycle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if cycle is None or cycle.id != link.cycle_id:
        raise FollowUpRefused("not_found", "This link is not for that filing.")
    if db.scalar(select(NilConfirmation.id).where(NilConfirmation.cycle_id == cycle.id)):
        raise FollowUpRefused("already_confirmed", "You have already told us; thank you.")
    if cycle.stage not in COLLECTING:
        raise FollowUpRefused(
            "not_collecting", "We are already working on this filing; nothing more is needed."
        )
    client = db.get(Client, cycle.client_pk)
    ticket = db.get(Ticket, cycle.ticket_id)
    filing = db.get(Filing, cycle.filing_id)
    if client is None or ticket is None or filing is None:  # foreign keys prevent it
        raise RuntimeError("cycle refers to missing rows")
    confirmation = NilConfirmation(
        cycle_id=cycle.id,
        client_pk=client.id,
        portal_link_id=link.id,
        note=(note or "").strip() or None,
        confirmed_at=utcnow(),
    )
    db.add(confirmation)
    db.flush()
    if cycle.stage is Stage.SCHEDULED:
        transition(db, cycle, ticket, "request_documents", actor_id=None, actor_role=None)
    transition(db, cycle, ticket, "no_activity", actor_id=None, actor_role=None)
    code = ticket_code(ticket.number)
    audit(
        db,
        "cycle.nil_confirmed",
        actor_role="client",
        target=("ticket", code),
        details={"portal_link": link.id, "with_note": confirmation.note is not None},
    )
    notify(
        db,
        kind=NotificationKind.NO_ACTIVITY,
        dedupe_key=f"nil:{cycle.id}",
        client_pk=client.id,
        cycle_id=cycle.id,
        user_id=client.owner_id,
        title=f"No activity: {client.legal_name}, {filing.code} {cycle.period_label}",
        body=f"The client confirmed no activity for {code}. It is now in review: check their "
        "confirmation and approve the nil filing.",
    )
    draft_updates(db, firm_name, client_pk=client.id)  # CR-03: a state change
    return confirmation


def record_phone_contact(
    db: Session,
    ticket: Ticket,
    user_id: int,
    actor_role: str,
    note: str,
    promised_on: date | None,
) -> PhoneContact:
    """Keep the call note; the caller has checked access and commits."""
    contact = PhoneContact(
        ticket_id=ticket.id,
        client_pk=ticket.client_pk,
        user_id=user_id,
        note=note,
        promised_on=promised_on,
        created_at=utcnow(),
    )
    db.add(contact)
    db.flush()
    # The note may hold personal details, so the audit keeps only that a call was logged.
    audit(
        db,
        "client.phone_contact",
        actor_user_id=user_id,
        actor_role=actor_role,
        target=("ticket", ticket_code(ticket.number)),
        details={"promised_on": str(promised_on) if promised_on else None},
    )
    return contact


@dataclass
class PromiseTally:
    reminded: int = 0
    settled: int = 0


def _still_waiting(db: Session, ticket: Ticket) -> bool:
    client = db.get(Client, ticket.client_pk)
    if client is None or client.status is not ClientStatus.ACTIVE:
        return False
    stage = db.scalar(select(Cycle.stage).where(Cycle.ticket_id == ticket.id))
    if stage is not None:
        return stage in COLLECTING
    status = db.scalar(select(Task.status).where(Task.ticket_id == ticket.id))
    return status is TaskStatus.OPEN


def phone_promises(factory: sessionmaker[Session], *, today: date) -> PromiseTally:
    """FR-IN-05: on the promised date, remind the person who took the call (once)."""
    tally = PromiseTally()
    with factory() as db:
        due = list(
            db.scalars(
                select(PhoneContact.id)
                .where(PhoneContact.reminded_at.is_(None), PhoneContact.promised_on <= today)
                .order_by(PhoneContact.promised_on, PhoneContact.id)
            )
        )
    for contact_id in due:
        with factory() as db:
            contact = db.scalar(
                select(PhoneContact)
                .where(PhoneContact.id == contact_id, PhoneContact.reminded_at.is_(None))
                .with_for_update()
            )
            ticket = db.get(Ticket, contact.ticket_id) if contact else None
            if contact is None or ticket is None:
                continue
            waiting = _still_waiting(db, ticket)
            if waiting:
                user = db.get(User, contact.user_id)
                client = db.get(Client, ticket.client_pk)
                name = client.legal_name if client else "the client"
                active = user is not None and user.is_active
                notify(
                    db,
                    kind=NotificationKind.PHONE_PROMISE,
                    dedupe_key=f"phone:{contact.id}",
                    client_pk=ticket.client_pk,
                    cycle_id=db.scalar(select(Cycle.id).where(Cycle.ticket_id == ticket.id)),
                    # Someone who has left: the Administrators pick it up instead.
                    **(
                        {"user_id": contact.user_id} if active else {"for_role": Role.ADMINISTRATOR}
                    ),
                    title=f"Promised today: {name}, {ticket_code(ticket.number)}",
                    body=f"On the call on {contact.created_at:%d %b %Y}, {name} promised"
                    f" {contact.promised_on:%d %b %Y}. It is still waiting. Your note:\n"
                    + contact.note,
                )
                tally.reminded += 1
            else:
                tally.settled += 1
            contact.reminded_at = utcnow()
            contact.reminder_sent = waiting
            db.commit()
    if tally.reminded or tally.settled:
        log.info(
            "phone promises",
            extra={"ctx": {"reminded": tally.reminded, "settled": tally.settled}},
        )
    return tally
