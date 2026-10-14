"""The email outbox, for the Administrator (BRD INT-01, FR-AL-06): what was sent, and how it went.

Staff see the emails for their own clients on each ticket (chunk C15); the full outbox, with
every client's email in it, is the Administrator's.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.service import ticket_code
from app.errors import ApiHTTPException
from app.mail.outbox import BadAddress, create
from app.mail.templates import render
from app.models import (
    Client,
    Email,
    EmailEvent,
    EmailKind,
    EmailStatus,
    EventSource,
    Role,
    Ticket,
)

router = APIRouter(prefix="/api/admin/emails", tags=["admin: email"])

admin_only = require_roles(Role.ADMINISTRATOR)


class ClientRef(BaseModel):
    client_id: int
    legal_name: str


class EmailRow(BaseModel):
    id: int
    kind: EmailKind
    status: EmailStatus
    to: list[str]
    subject: str
    client: ClientRef | None
    ticket: str | None
    attempts: int
    last_error: str | None
    created_at: datetime
    queued_at: datetime | None
    sent_at: datetime | None
    delivered_at: datetime | None
    failed_at: datetime | None


class PageOut(BaseModel):
    items: list[EmailRow]
    total: int
    offset: int
    limit: int


class EventOut(BaseModel):
    status: EmailStatus
    source: EventSource
    detail: str | None
    occurred_at: datetime


class EmailDetail(EmailRow):
    cc: list[str]
    body: str
    next_attempt_at: datetime | None
    events: list[EventOut]


class TestIn(BaseModel):
    to: EmailStr | None = None  # None: the Administrator's own address


def _row(email: Email, client: Client | None, ticket_number: int | None) -> EmailRow:
    return EmailRow(
        id=email.id,
        kind=email.kind,
        status=email.status,
        to=list(email.to_addresses),
        subject=email.subject,
        client=ClientRef(client_id=client.client_id, legal_name=client.legal_name)
        if client
        else None,
        ticket=ticket_code(ticket_number) if ticket_number is not None else None,
        attempts=email.attempts,
        last_error=email.last_error,
        created_at=email.created_at,
        queued_at=email.queued_at,
        sent_at=email.sent_at,
        delivered_at=email.delivered_at,
        failed_at=email.failed_at,
    )


def _detail(db: Session, email: Email) -> EmailDetail:
    client = db.get(Client, email.client_pk) if email.client_pk else None
    ticket = db.get(Ticket, email.ticket_id) if email.ticket_id else None
    events = db.scalars(
        select(EmailEvent).where(EmailEvent.email_id == email.id).order_by(EmailEvent.id)
    )
    return EmailDetail(
        **_row(email, client, ticket.number if ticket else None).model_dump(),
        cc=list(email.cc_addresses),
        body=email.body_text,
        next_attempt_at=email.next_attempt_at,
        events=[EventOut.model_validate(e, from_attributes=True) for e in events],
    )


@router.get("")
def list_emails(
    _: Principal = Depends(admin_only),  # noqa: B008
    status: EmailStatus | None = None,
    kind: EmailKind | None = None,
    offset: int = Query(0, ge=0, le=100_000),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),  # noqa: B008
) -> PageOut:
    """Newest first. Drafts are left out unless asked for: they have not been sent."""
    where = []
    if status is not None:
        where.append(Email.status == status)
    else:
        where.append(Email.status != EmailStatus.DRAFT)
    if kind is not None:
        where.append(Email.kind == kind)
    total = db.scalar(select(func.count()).select_from(Email).where(*where)) or 0
    rows = db.execute(
        select(Email, Client, Ticket.number)
        .outerjoin(Client, Client.id == Email.client_pk)
        .outerjoin(Ticket, Ticket.id == Email.ticket_id)
        .where(*where)
        .order_by(Email.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return PageOut(
        items=[_row(e, c, n) for e, c, n in rows], total=total, offset=offset, limit=limit
    )


@router.get("/{email_id}")
def get_email(
    email_id: int,
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> EmailDetail:
    email = db.get(Email, email_id)
    if email is None:
        raise ApiHTTPException(404, "No such email.", code="not_found")
    return _detail(db, email)


@router.post("/test", status_code=201)
def send_test(
    body: TestIn,
    request: Request,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> EmailDetail:
    """Queue a test email, to check the mail settings end to end."""
    content = render(
        "test",
        {"firm_name": request.app.state.settings.firm_name, "sender_name": actor.full_name},
    )
    try:
        email = create(
            db,
            kind=EmailKind.TEST,
            to=[str(body.to) if body.to else actor.email],
            content=content,
            created_by_id=actor.user_id,
            queue_now=True,
        )
    except BadAddress as exc:
        raise ApiHTTPException(422, str(exc), code="bad_address") from None
    audit(
        db,
        "email.test_queued",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("email", email.id),
        details={"to_self": body.to is None},
    )
    db.commit()
    return _detail(db, email)
