"""Private client links on a filing ticket (BRD FR-AL-02): staff make, send and withdraw them.

Only the client's owner and the Administrator, like every other change to a ticket. A new link
is shown once, so it can be copied into a message by hand, and can also be emailed to the
client's primary contact. Making a new link stops earlier unopened ones from working.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.config import Settings
from app.errors import ApiHTTPException
from app.models import Client, ClientStatus, Cycle, LinkOrigin, PortalLink, Role, Stage, User
from app.portal.links import (
    email_link,
    end_session,
    issue,
    mask_address,
    primary_address,
    status_of,
    url_for,
)
from app.routers.tickets import _load, can_edit

router = APIRouter(tags=["tickets"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)

STATUS_LABEL = {
    "waiting": "Not opened yet",
    "opened": "Opened",
    "ended": "Ended",
    "expired": "Expired",
    "replaced": "Replaced by a newer link",
    "withdrawn": "Withdrawn",
}


class NewLinkIn(BaseModel):
    send_email: bool = True


class LinkOut(BaseModel):
    id: int
    status: str
    status_label: str
    origin: LinkOrigin
    created_by: str | None
    created_at: datetime
    expires_at: datetime
    opened_at: datetime | None
    last_seen_at: datetime | None


class NewLinkOut(BaseModel):
    link: LinkOut
    url: str  # shown this once; only its hash is kept
    emailed_to: str | None  # masked


def _link_out(link: PortalLink, by: str | None) -> LinkOut:
    status = status_of(link)
    return LinkOut(
        id=link.id,
        status=status,
        status_label=STATUS_LABEL[status],
        origin=link.origin,
        created_by=by,
        created_at=link.created_at,
        expires_at=link.expires_at,
        opened_at=link.used_at,
        last_seen_at=link.last_seen_at,
    )


def _cycle_for_change(db: Session, principal: Principal, ref: str) -> tuple[Client, Cycle, int]:
    ticket, client = _load(db, principal, ref, lock=True)
    if not can_edit(principal, client):
        raise ApiHTTPException(403, "Only the client's owner can do this.", code="forbidden")
    cycle = db.scalar(select(Cycle).where(Cycle.ticket_id == ticket.id))
    if cycle is None:
        raise ApiHTTPException(409, "Task tickets have no client link.", code="not_a_cycle")
    return client, cycle, ticket.id


@router.get("/api/tickets/{ticket}/links")
def list_links(
    ticket: str,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[LinkOut]:
    found, client = _load(db, principal, ticket)
    if not can_edit(principal, client):
        raise ApiHTTPException(403, "Only the client's owner can see its links.", code="forbidden")
    cycle_id = db.scalar(select(Cycle.id).where(Cycle.ticket_id == found.id))
    if cycle_id is None:
        return []
    rows = db.execute(
        select(PortalLink, User.full_name)
        .outerjoin(User, User.id == PortalLink.created_by_id)
        .where(PortalLink.cycle_id == cycle_id)
        .order_by(PortalLink.id.desc())
        .limit(50)
    )
    return [_link_out(link, name) for link, name in rows]


@router.post("/api/tickets/{ticket}/links", status_code=201)
def new_link(
    ticket: str,
    body: NewLinkIn,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> NewLinkOut:
    """FR-AL-02: make (or renew) the client's link for this cycle, and optionally email it."""
    settings: Settings = request.app.state.settings
    client, cycle, ticket_id = _cycle_for_change(db, principal, ticket)
    if client.status is ClientStatus.CLOSED or cycle.stage is Stage.CLOSED:
        raise ApiHTTPException(409, "This work is closed; no link can be made.", code="closed")
    address = primary_address(db, client) if body.send_email else None
    if body.send_email and address is None:
        raise ApiHTTPException(409, "The client has no primary contact email.", code="no_contact")
    token, link = issue(
        db,
        settings,
        client=client,
        cycle=cycle,
        origin=LinkOrigin.STAFF,
        created_by_id=principal.user_id,
    )
    if address is not None:
        email_link(
            db,
            settings,
            link=link,
            token=token,
            client=client,
            cycle=cycle,
            to=address,
            created_by_id=principal.user_id,
        )
    audit(
        db,
        "portal.link_created",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("ticket", ticket_id),
        details={"link": link.id, "emailed": address is not None},
    )
    db.commit()
    return NewLinkOut(
        link=_link_out(link, principal.full_name),
        url=url_for(settings, token),
        emailed_to=mask_address(address) if address else None,
    )


@router.post("/api/tickets/{ticket}/links/{link_id}/withdraw")
def withdraw_link(
    ticket: str,
    link_id: int,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> LinkOut:
    """Stop a link (and any portal session it started) from working, at once."""
    _, cycle, ticket_id = _cycle_for_change(db, principal, ticket)
    link = db.scalar(
        select(PortalLink)
        .where(PortalLink.id == link_id, PortalLink.cycle_id == cycle.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if link is None:
        raise ApiHTTPException(404, "No such link on this ticket.", code="not_found")
    if link.revoked_at is None:
        end_session(link, "withdrawn")
        audit(
            db,
            "portal.link_withdrawn",
            actor_user_id=principal.user_id,
            actor_role=principal.role.value,
            target=("ticket", ticket_id),
            details={"link": link.id},
        )
    db.commit()
    by = db.get(User, link.created_by_id) if link.created_by_id else None
    return _link_out(link, by.full_name if by else None)
