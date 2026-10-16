"""Client updates (CR-03): the drafts each state change makes, previewed and sent by hand.

Only the client's owner and the Administrator (FR-AC-01). Sending is the one way a client
update leaves the system, and it is audited.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.access import RecordAccess, authorize_record
from app.auth.deps import Principal, get_db, require_roles
from app.config import Settings
from app.errors import ApiHTTPException
from app.mail.problems import CLIENT_KINDS, problem_of
from app.mail.problems import LABEL as PROBLEM_LABEL
from app.mail.updates import UpdateRefused, prepare, send_update
from app.models import (
    Client,
    Email,
    EmailKind,
    EmailStatus,
    Role,
    StateEvent,
    User,
)
from app.routers.tickets import _load
from app.timeutil import utcnow

router = APIRouter(tags=["client updates"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class SendIn(BaseModel):
    note: Note | None = None
    cc_contact_ids: list[int] = Field(default_factory=list, max_length=20)


class ContactOut(BaseModel):
    id: int
    name: str
    email: str


class PreviewOut(BaseModel):
    subject: str
    body: str
    to: list[str]
    cc: list[str]
    other_contacts: list[ContactOut]


class UpdateOut(BaseModel):
    id: int
    status: EmailStatus
    change: str  # e.g. "preparing -> in_review", for the label
    from_state: str
    to_state: str
    subject: str
    to: list[str]
    cc: list[str]
    created_at: datetime
    queued_at: datetime | None
    sent_by: str | None
    sent_at: datetime | None
    delivered_at: datetime | None
    failed_at: datetime | None


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _rows(db: Session, *where: ColumnElement[bool]) -> list[UpdateOut]:
    rows = db.execute(
        select(Email, StateEvent, User.full_name)
        .join(StateEvent, StateEvent.id == Email.state_event_id)
        .outerjoin(User, User.id == Email.queued_by_id)
        .where(Email.kind == EmailKind.CLIENT_UPDATE, *where)
        .order_by(Email.id.desc())
        .limit(100)
    )
    return [
        UpdateOut(
            id=e.id,
            status=e.status,
            change=f"{ev.from_state} -> {ev.to_state}",
            from_state=ev.from_state,
            to_state=ev.to_state,
            subject=e.subject,
            to=list(e.to_addresses),
            cc=list(e.cc_addresses),
            created_at=e.created_at,
            queued_at=e.queued_at,
            sent_by=name,
            sent_at=e.sent_at,
            delivered_at=e.delivered_at,
            failed_at=e.failed_at,
        )
        for e, ev, name in rows
    ]


def _editable_client(principal: Principal, client: Client | None) -> Client:
    """The client, if this person may send its updates: its owner or the Administrator."""
    if client is None:
        raise ApiHTTPException(404, "Not found.", code="not_found")
    authorize_record(principal, RecordAccess(owner_id=client.owner_id), what="client")
    return client


def _email(db: Session, principal: Principal, email_id: int) -> Email:
    email = db.get(Email, email_id)
    if email is None or email.kind is not EmailKind.CLIENT_UPDATE or email.client_pk is None:
        raise ApiHTTPException(404, "No such update.", code="not_found")
    _editable_client(principal, db.get(Client, email.client_pk))
    return email


@router.get("/api/tickets/{ticket}/updates")
def ticket_updates(
    ticket: str,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[UpdateOut]:
    found, client = _load(db, principal, ticket)
    _editable_client(principal, client)
    return _rows(db, Email.ticket_id == found.id)


@router.get("/api/clients/{client_id}/updates")
def client_updates(
    client_id: int,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[UpdateOut]:
    """The client's status-change updates (ticket updates are listed on each ticket)."""
    client = _editable_client(
        principal, db.scalar(select(Client).where(Client.client_id == client_id))
    )
    return _rows(db, Email.client_pk == client.id, Email.ticket_id.is_(None))


@router.post("/api/updates/{email_id}/preview")
def preview_update(
    email_id: int,
    body: SendIn,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PreviewOut:
    """The email exactly as Send would send it now. Changes nothing."""
    email = _email(db, principal, email_id)
    try:
        ready = prepare(
            db,
            email,
            firm_name=_settings(request).firm_name,
            sender_name=principal.full_name,
            note=body.note,
            cc_contact_ids=body.cc_contact_ids,
        )
    except UpdateRefused as exc:
        raise ApiHTTPException(exc.status, str(exc), code=exc.code) from None
    return PreviewOut(
        subject=ready.content.subject,
        body=ready.content.body,
        to=ready.to,
        cc=ready.cc,
        other_contacts=[ContactOut(id=c.id, name=c.name, email=c.email) for c in ready.others],
    )


@router.post("/api/updates/{email_id}/send")
def send(
    email_id: int,
    body: SendIn,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> UpdateOut:
    """CR-03: Send update to client. Once only: a second click finds it already sent."""
    email = _email(db, principal, email_id)
    try:
        ready = send_update(
            db,
            email.id,
            firm_name=_settings(request).firm_name,
            sender_id=principal.user_id,
            sender_name=principal.full_name,
            note=body.note,
            cc_contact_ids=body.cc_contact_ids,
            settings=_settings(request),
        )
    except UpdateRefused as exc:
        db.rollback()
        raise ApiHTTPException(exc.status, str(exc), code=exc.code) from None
    client = db.get(Client, email.client_pk)
    audit(
        db,
        "client_update.sent",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("email", email.id),
        details={
            "client_id": client.client_id if client else None,
            "ticket": email.ticket_id,
            "copied": len(ready.cc),
            "with_note": bool((body.note or "").strip()),
        },
    )
    db.commit()
    return _rows(db, Email.id == email.id)[0]


KIND_LABEL = {
    EmailKind.CLIENT_ALERT: "Alert",
    EmailKind.CLIENT_UPDATE: "Update",
    EmailKind.PORTAL_LINK: "Private link",
    EmailKind.FEE_REMINDER: "Fee reminder",
}


class ClientEmailOut(BaseModel):
    id: int
    kind: EmailKind
    kind_label: str
    status: EmailStatus
    subject: str
    to: list[str]
    queued_at: datetime | None
    attempts: int
    last_error: str | None
    problem: str | None  # failed or delayed, until followed up (FR-AL-06)
    problem_label: str | None
    followed_up_by: str | None
    followed_up_at: datetime | None


def _client_email_out(email: Email, followed_by: str | None) -> ClientEmailOut:
    problem = problem_of(email)
    return ClientEmailOut(
        id=email.id,
        kind=email.kind,
        kind_label=KIND_LABEL.get(email.kind, email.kind.value),
        status=email.status,
        subject=email.subject,
        to=list(email.to_addresses),
        queued_at=email.queued_at,
        attempts=email.attempts,
        last_error=email.last_error,
        problem=problem,
        problem_label=PROBLEM_LABEL.get(problem or ""),
        followed_up_by=followed_by,
        followed_up_at=email.problem_handled_at,
    )


@router.get("/api/tickets/{ticket}/emails")
def ticket_emails(
    ticket: str,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ClientEmailOut]:
    """FR-AL-06: every email that went to the client about this ticket, and how it went."""
    found, client = _load(db, principal, ticket)
    _editable_client(principal, client)
    rows = db.execute(
        select(Email, User.full_name)
        .outerjoin(User, User.id == Email.problem_handled_by_id)
        .where(
            Email.ticket_id == found.id,
            Email.kind.in_(CLIENT_KINDS),
            Email.status != EmailStatus.DRAFT,
        )
        .order_by(Email.id.desc())
        .limit(100)
    )
    return [_client_email_out(e, name) for e, name in rows]


@router.post("/api/emails/{email_id}/followed-up")
def mark_followed_up(
    email_id: int,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ClientEmailOut:
    """The owner has called the client about a failed or delayed email: take it off the list."""
    email = db.scalar(
        select(Email)
        .where(Email.id == email_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if email is None or email.kind not in CLIENT_KINDS or email.client_pk is None:
        raise ApiHTTPException(404, "No such email.", code="not_found")
    _editable_client(principal, db.get(Client, email.client_pk))
    if problem_of(email) is None:
        raise ApiHTTPException(409, "This email needs no follow-up.", code="no_problem")
    email.problem_handled_at = utcnow()
    email.problem_handled_by_id = principal.user_id
    audit(
        db,
        "email.followed_up",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("email", email.id),
        details={"status": email.status},
    )
    db.commit()
    return _client_email_out(email, principal.full_name)
