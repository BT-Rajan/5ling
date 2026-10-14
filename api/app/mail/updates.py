"""Client updates (CR-03): every state change gets one draft email; only a person sends it.

A state change (a cycle stage, a task status or a client status) writes a `StateEvent` in the
same transaction (chunk C09). `draft_updates` turns each unhandled event into exactly one draft
email to the client's primary contact and marks the event handled. The unique `state_event_id`
on emails and the once-only `handled_at` trigger on events make a second draft impossible, even
if two callers race. The endpoints that change state call it before they commit, so the draft
exists the moment the change does; the send worker also sweeps up any event that slipped past.

Nothing here sends. `send_update` queues one draft after a person presses Send: the row is
locked and must still be a draft, so a double click queues it once. It is re-rendered at that
moment with the sender's name and note, to the current primary contact, plus any other contacts
the sender chose.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.cycles.service import ticket_code
from app.cycles.stages import CLIENT_STATUS_LABEL, STAGE_LABEL, TASK_LABEL
from app.mail.outbox import clean_addresses, create, queue
from app.mail.templates import Rendered, render
from app.models import (
    Client,
    ClientStatus,
    Contact,
    Cycle,
    Email,
    EmailKind,
    EmailStatus,
    EventKind,
    Filing,
    Stage,
    StateEvent,
    TaskStatus,
    Ticket,
)
from app.timeutil import utcnow


class UpdateRefused(ValueError):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _label(kind: EventKind, state: str) -> str:
    if kind is EventKind.CYCLE_STAGE:
        return STAGE_LABEL[Stage(state)]
    if kind is EventKind.TASK_STATUS:
        return TASK_LABEL[TaskStatus(state)]
    return CLIENT_STATUS_LABEL[ClientStatus(state)]


def content_for(
    db: Session,
    event: StateEvent,
    *,
    firm_name: str,
    sender_name: str | None = None,
    note: str | None = None,
) -> Rendered:
    client = db.get(Client, event.client_pk)
    if client is None:  # a foreign key prevents it
        raise RuntimeError("state event refers to a missing client")
    values: dict[str, str | int | date | None] = {
        "firm_name": firm_name,
        "client_name": client.legal_name,
        "client_id": client.client_id,
        "stage": _label(event.kind, event.to_state),
        "previous_stage": _label(event.kind, event.from_state),
        "note": note,
        "sender_name": sender_name,
    }
    if event.kind is EventKind.CLIENT_STATUS:
        return render("client_status_change", values)
    ticket = db.get(Ticket, event.ticket_id) if event.ticket_id else None
    if ticket is None:  # ticket events always name their ticket
        raise RuntimeError("state event refers to a missing ticket")
    values["ticket_id"] = ticket_code(ticket.number)
    if event.kind is EventKind.TASK_STATUS:
        values["detail"] = ticket.title
        return render("task_status_change", values)
    cycle = db.scalar(select(Cycle).where(Cycle.ticket_id == ticket.id))
    filing = db.get(Filing, cycle.filing_id) if cycle else None
    if cycle is None or filing is None:
        raise RuntimeError("stage event refers to a missing cycle")
    values |= {"filing": filing.code, "period": cycle.period_label, "due_date": cycle.due_date}
    return render("stage_change", values)


def primary_contact(db: Session, client_pk: int) -> Contact | None:
    return db.scalar(
        select(Contact).where(Contact.client_pk == client_pk, Contact.is_primary.is_(True))
    )


def draft_updates(db: Session, firm_name: str, *, client_pk: int | None = None) -> list[Email]:
    """One draft per unhandled state event, in `db`'s transaction. Returns the new drafts."""
    db.flush()  # include events added in this transaction
    stmt = select(StateEvent).where(StateEvent.handled_at.is_(None)).order_by(StateEvent.id)
    if client_pk is not None:
        stmt = stmt.where(StateEvent.client_pk == client_pk)
    made: list[Email] = []
    now = utcnow()
    for event in db.scalars(stmt.limit(500).with_for_update(skip_locked=True)):
        contact = primary_contact(db, event.client_pk)
        if contact is not None:  # every client has one (C07); without one there is no one to tell
            made.append(
                create(
                    db,
                    kind=EmailKind.CLIENT_UPDATE,
                    to=[contact.email],
                    content=content_for(db, event, firm_name=firm_name),
                    client_pk=event.client_pk,
                    ticket_id=event.ticket_id,
                    state_event_id=event.id,
                )
            )
        event.handled_at = now
    return made


@dataclass(frozen=True)
class Prepared:
    email: Email
    content: Rendered
    to: list[str]
    cc: list[str]
    others: list[Contact]  # contacts that can be copied in


def prepare(
    db: Session,
    email: Email,
    *,
    firm_name: str,
    sender_name: str,
    note: str | None,
    cc_contact_ids: Iterable[int] = (),
) -> Prepared:
    """The email as it would be sent now: re-rendered, to the current primary contact."""
    if email.kind is not EmailKind.CLIENT_UPDATE or email.state_event_id is None:
        raise UpdateRefused("not_an_update", "This is not a client update.", 404)
    event = db.get(StateEvent, email.state_event_id)
    if event is None or email.client_pk is None:
        raise RuntimeError("client update without its state event")
    contacts = list(
        db.scalars(
            select(Contact).where(Contact.client_pk == email.client_pk).order_by(Contact.position)
        )
    )
    primary = next((c for c in contacts if c.is_primary), None)
    if primary is None:
        raise UpdateRefused("no_contact", "The client has no primary contact.")
    others = [c for c in contacts if not c.is_primary]
    wanted = set(cc_contact_ids)
    unknown = wanted - {c.id for c in others}
    if unknown:
        raise UpdateRefused("bad_contact", "Copy only this client's other contacts.", 422)
    note = (note or "").strip() or None
    content = content_for(db, event, firm_name=firm_name, sender_name=sender_name, note=note)
    to = clean_addresses([primary.email])
    cc = [a for a in clean_addresses(c.email for c in others if c.id in wanted) if a not in to]
    return Prepared(email=email, content=content, to=to, cc=cc, others=others)


def send_update(
    db: Session,
    email_id: int,
    *,
    firm_name: str,
    sender_id: int,
    sender_name: str,
    note: str | None,
    cc_contact_ids: Iterable[int] = (),
) -> Prepared:
    """Queue one draft (CR-03). The caller has checked access, and audits and commits."""
    email = db.scalar(
        select(Email)
        .where(Email.id == email_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if email is None:
        raise UpdateRefused("not_found", "No such email.", 404)
    if email.status is not EmailStatus.DRAFT:
        raise UpdateRefused("already_sent", "This update has already been sent.")
    ready = prepare(
        db,
        email,
        firm_name=firm_name,
        sender_name=sender_name,
        note=note,
        cc_contact_ids=cc_contact_ids,
    )
    email.to_addresses = ready.to
    email.cc_addresses = ready.cc
    email.subject = ready.content.subject
    email.body_text = ready.content.body
    db.flush()  # the text is written while it is still a draft; queueing then freezes it
    queue(db, email, by_id=sender_id)
    return ready


def sweep(factory: sessionmaker[Session], firm_name: str) -> int:
    """Draft any update a state change is still waiting for. Run by the send worker each round."""
    with factory() as db:
        made = draft_updates(db, firm_name)
        db.commit()
        return len(made)
