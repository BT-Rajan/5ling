"""Notifications for the signed-in person (BRD FR-IN-01 to FR-IN-03).

A person sees notifications addressed to them, and those addressed to their role (escalations
for Administrators). Staff see a notification only while they still own its client, so a
reassigned client's news goes with the client. Dismissing an escalation needs a note, which is
kept with it and in the audit log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, StringConstraints
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.service import ticket_code
from app.errors import ApiHTTPException
from app.models import Client, Cycle, Notification, NotificationKind, Role, Ticket, User
from app.timeutil import utcnow

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
MIN_NOTE = 3


class NotificationOut(BaseModel):
    id: int
    kind: NotificationKind
    title: str
    body: str
    client_id: int
    ticket: str | None
    created_at: datetime
    read: bool
    dismissed_at: datetime | None
    dismissed_by: str | None
    dismiss_note: str | None
    needs_note: bool  # escalations cannot be dismissed without one


class PageOut(BaseModel):
    items: list[NotificationOut]
    unread: int


class CountOut(BaseModel):
    unread: int
    open_escalations: int


class DismissIn(BaseModel):
    note: Note | None = None


def _mine(principal: Principal) -> ColumnElement[bool]:
    addressed = or_(
        Notification.user_id == principal.user_id, Notification.for_role == principal.role
    )
    if principal.role is Role.ADMINISTRATOR:
        return addressed
    return and_(addressed, Client.owner_id == principal.user_id)


def _out(n: Notification, client_id: int, number: int | None, by: str | None) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        kind=n.kind,
        title=n.title,
        body=n.body,
        client_id=client_id,
        ticket=ticket_code(number) if number is not None else None,
        created_at=n.created_at,
        read=n.read_at is not None,
        dismissed_at=n.dismissed_at,
        dismissed_by=by,
        dismiss_note=n.dismiss_note,
        needs_note=n.kind is NotificationKind.ESCALATION,
    )


@router.get("")
def list_notifications(
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    show: Literal["open", "all"] = "open",
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),  # noqa: B008
) -> PageOut:
    """Newest first. "open": not yet dismissed."""
    stmt = (
        select(Notification, Client.client_id, Ticket.number, User.full_name)
        .join(Client, Client.id == Notification.client_pk)
        .outerjoin(Cycle, Cycle.id == Notification.cycle_id)
        .outerjoin(Ticket, Ticket.id == Cycle.ticket_id)
        .outerjoin(User, User.id == Notification.dismissed_by_id)
        .where(_mine(principal))
    )
    if show == "open":
        stmt = stmt.where(Notification.dismissed_at.is_(None))
    rows = db.execute(stmt.order_by(Notification.id.desc()).limit(limit)).all()
    return PageOut(items=[_out(*r) for r in rows], unread=_unread(db, principal))


def _unread(db: Session, principal: Principal) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Notification)
            .join(Client, Client.id == Notification.client_pk)
            .where(
                _mine(principal),
                Notification.read_at.is_(None),
                Notification.dismissed_at.is_(None),
            )
        )
        or 0
    )


@router.get("/count")
def count(
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> CountOut:
    """For the bell: unread notifications, and escalations nobody has dismissed yet."""
    escalations = 0
    if principal.role is Role.ADMINISTRATOR:
        escalations = int(
            db.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.kind == NotificationKind.ESCALATION,
                    Notification.dismissed_at.is_(None),
                )
            )
            or 0
        )
    return CountOut(unread=_unread(db, principal), open_escalations=escalations)


def _load(
    db: Session, principal: Principal, notification_id: int
) -> tuple[Notification, int, int | None]:
    row = db.execute(
        select(Notification, Client.client_id, Ticket.number)
        .join(Client, Client.id == Notification.client_pk)
        .outerjoin(Cycle, Cycle.id == Notification.cycle_id)
        .outerjoin(Ticket, Ticket.id == Cycle.ticket_id)
        .where(Notification.id == notification_id, _mine(principal))
        .with_for_update(of=Notification)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise ApiHTTPException(404, "No such notification.", code="not_found")
    return row[0], row[1], row[2]


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> NotificationOut:
    n, client_id, number = _load(db, principal, notification_id)
    if n.read_at is None:
        n.read_at = utcnow()
        db.commit()
    return _out(n, client_id, number, None)


@router.post("/{notification_id}/dismiss")
def dismiss(
    notification_id: int,
    body: DismissIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> NotificationOut:
    """Take it off the list. An escalation needs a note saying what was done (FR-IN-03)."""
    n, client_id, number = _load(db, principal, notification_id)
    if n.dismissed_at is not None:
        raise ApiHTTPException(409, "This has already been dismissed.", code="already_dismissed")
    note = (body.note or "").strip() or None
    if n.kind is NotificationKind.ESCALATION and (note is None or len(note) < MIN_NOTE):
        raise ApiHTTPException(
            422, "Add a note saying what was done about it.", code="note_required"
        )
    now = utcnow()
    n.read_at = n.read_at or now
    n.dismissed_at = now
    n.dismissed_by_id = principal.user_id
    n.dismiss_note = note
    audit(
        db,
        "escalation.dismissed"
        if n.kind is NotificationKind.ESCALATION
        else "notification.dismissed",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("notification", n.id),
        details={"kind": n.kind, "note": note},
    )
    db.commit()
    return _out(n, client_id, number, principal.full_name)
