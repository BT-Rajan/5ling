"""Private client links (BRD FR-AL-02): issuing, opening, portal sessions and new-link requests.

See `app.models.portal` for the rules. The link goes in the URL fragment (`/portal#t=...`), which
browsers never send to a server, so it stays out of server and proxy logs and Referer headers;
the page posts it to /api/portal/open.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.cycles.service import ticket_code
from app.mail.outbox import create
from app.mail.templates import render
from app.models import (
    Client,
    ClientStatus,
    Contact,
    Cycle,
    Email,
    EmailKind,
    Filing,
    LinkOrigin,
    PortalLink,
    Stage,
    Ticket,
)
from app.rules import rules
from app.timeutil import ensure_utc, format_ist, utcnow

CLIENT_REQUESTS_PER_DAY = 3  # new links a client can ask for, per cycle


class LinkProblem(Exception):
    """Why a link or portal session cannot be used, in words for the client."""

    def __init__(self, code: str, message: str, status: int, can_request_new: bool) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.can_request_new = can_request_new


def invalid() -> LinkProblem:
    return LinkProblem(
        "link_invalid",
        "This link is not valid. Check that you used the whole link from your email.",
        404,
        False,
    )


def session_ended() -> LinkProblem:
    return LinkProblem(
        "portal_session_ended",
        "Your private link has ended. Open the newest link from your email, or ask us for a new one.",
        401,
        False,
    )


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cookie_name(settings: Settings) -> str:
    return "__Host-ll_portal" if settings.is_production else "ll_portal"


def csrf_for(settings: Settings, session_token: str) -> str:
    """Derived from the session, so nothing extra is stored."""
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, b"portal-csrf:" + session_token.encode(), hashlib.sha256).hexdigest()


def url_for(settings: Settings, token: str) -> str:
    return f"{settings.public_url}/portal#t={token}"


def mask_address(address: str) -> str:
    """a•••@example.in: enough for the client to recognise, not enough to learn it."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}•••@{domain}"


def _closed(client: Client, cycle: Cycle) -> bool:
    return client.status is ClientStatus.CLOSED or cycle.stage is Stage.CLOSED


def primary_address(db: Session, client: Client) -> str | None:
    return db.scalar(
        select(Contact.email).where(Contact.client_pk == client.id, Contact.is_primary.is_(True))
    )


def issue(
    db: Session,
    settings: Settings,
    *,
    client: Client,
    cycle: Cycle,
    origin: LinkOrigin,
    created_by_id: int | None,
    requested_from_id: int | None = None,
) -> tuple[str, PortalLink]:
    """A new link for the cycle. Earlier links not yet opened stop working: only the newest one
    in the client's inbox should."""
    now = utcnow()
    db.execute(
        update(PortalLink)
        .where(
            PortalLink.cycle_id == cycle.id,
            PortalLink.used_at.is_(None),
            PortalLink.revoked_at.is_(None),
        )
        .values(revoked_at=now, revoked_reason="replaced")
    )
    token = secrets.token_urlsafe(32)
    link = PortalLink(
        token_hash=hash_secret(token),
        client_pk=client.id,
        cycle_id=cycle.id,
        origin=origin,
        created_by_id=created_by_id,
        created_at=now,
        expires_at=now + timedelta(hours=rules(db).portal_link_hours),
        requested_from_id=requested_from_id,
    )
    db.add(link)
    db.flush()
    return token, link


def email_link(
    db: Session,
    settings: Settings,
    *,
    link: PortalLink,
    token: str,
    client: Client,
    cycle: Cycle,
    to: str,
    created_by_id: int | None,
) -> Email:
    ticket = db.get(Ticket, cycle.ticket_id)
    filing = db.get(Filing, cycle.filing_id)
    if ticket is None or filing is None:  # foreign keys prevent it
        raise RuntimeError("cycle refers to missing rows")
    content = render(
        "portal_link",
        {
            "firm_name": settings.firm_name,
            "client_name": client.legal_name,
            "client_id": client.client_id,
            "ticket_id": ticket_code(ticket.number),
            "filing": filing.code,
            "period": cycle.period_label,
            "due_date": cycle.due_date,
            "link": url_for(settings, token),
            "expires": format_ist(ensure_utc(link.expires_at), "%a %d %b %Y, %H:%M IST"),
        },
    )
    return create(
        db,
        kind=EmailKind.PORTAL_LINK,
        to=[to],
        content=content,
        client_pk=client.id,
        ticket_id=ticket.id,
        created_by_id=created_by_id,
        queue_now=True,
    )


@dataclass(frozen=True)
class Opened:
    link: PortalLink
    client: Client
    cycle: Cycle
    session_token: str


def _owners(db: Session, link: PortalLink) -> tuple[Client, Cycle]:
    client = db.get(Client, link.client_pk)
    cycle = db.get(Cycle, link.cycle_id)
    if client is None or cycle is None:  # foreign keys prevent it
        raise RuntimeError("link refers to missing rows")
    return client, cycle


def _why_not(link: PortalLink, client: Client, cycle: Cycle, now: datetime) -> LinkProblem | None:
    if _closed(client, cycle):
        return LinkProblem(
            "link_closed",
            "This work is finished, so the link no longer opens. Contact us if you need anything.",
            410,
            False,
        )
    if link.used_at is not None:
        return LinkProblem(
            "link_used",
            "This link has already been opened. Each link opens once, on one device.",
            410,
            True,
        )
    if link.revoked_at is not None:
        return LinkProblem(
            "link_withdrawn",
            "This link has been replaced or withdrawn. Use the newest link in your email.",
            410,
            True,
        )
    if ensure_utc(link.expires_at) <= now:
        return LinkProblem("link_expired", "This link has expired.", 410, True)
    return None


def find(db: Session, token: str, *, lock: bool = False) -> PortalLink:
    if not token or len(token) > 100:
        raise invalid()
    stmt = select(PortalLink).where(PortalLink.token_hash == hash_secret(token))
    link = db.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if lock else stmt
    )
    if link is None:
        raise invalid()
    return link


def open_link(db: Session, token: str) -> Opened:
    """Use the link up and start a portal session. Raises LinkProblem. The caller commits."""
    link = find(db, token, lock=True)  # locked: two clicks cannot both open it
    client, cycle = _owners(db, link)
    now = utcnow()
    problem = _why_not(link, client, cycle, now)
    if problem is not None:
        raise problem
    session_token = secrets.token_urlsafe(32)
    link.used_at = now
    link.last_seen_at = now
    link.session_hash = hash_secret(session_token)
    return Opened(link=link, client=client, cycle=cycle, session_token=session_token)


def refusal_for(db: Session, token: str) -> tuple[PortalLink, LinkProblem] | None:
    """Why a known link cannot be opened, for the record (None for an unknown token)."""
    try:
        link = find(db, token)
    except LinkProblem:
        return None
    client, cycle = _owners(db, link)
    problem = _why_not(link, client, cycle, utcnow())
    return (link, problem) if problem else None


def load_session(db: Session, session_token: str | None) -> tuple[PortalLink, Client, Cycle]:
    """The portal session behind the cookie, if it is still good. Raises session_ended()."""
    if not session_token or len(session_token) > 100:
        raise session_ended()
    link = db.scalar(
        select(PortalLink).where(PortalLink.session_hash == hash_secret(session_token))
    )
    if link is None or link.revoked_at is not None:
        raise session_ended()
    client, cycle = _owners(db, link)
    if _closed(client, cycle) or ensure_utc(link.expires_at) <= utcnow():
        raise session_ended()
    return link, client, cycle


def end_session(link: PortalLink, reason: str = "signed_out") -> None:
    if link.revoked_at is None:
        link.revoked_at = utcnow()
        link.revoked_reason = reason


def request_new(db: Session, settings: Settings, token: str) -> tuple[PortalLink, str]:
    """A client with a used, expired or replaced link asks for a new one. It is emailed to the
    primary contact on file, never shown, so a forwarded link gives nobody else access.

    Returns the new link and the masked address it went to.
    """
    old = find(db, token, lock=True)
    client, cycle = _owners(db, old)
    problem = _why_not(old, client, cycle, utcnow())
    if problem is None:
        raise LinkProblem("link_still_valid", "This link still works: open it.", 409, False)
    if not problem.can_request_new:
        raise problem
    if db.scalar(select(PortalLink.id).where(PortalLink.requested_from_id == old.id)):
        raise LinkProblem(
            "already_requested",
            "A new link was already sent for this one. Check your email, including spam.",
            409,
            False,
        )
    since = utcnow() - timedelta(hours=24)
    recent = db.scalar(
        select(func.count())
        .select_from(PortalLink)
        .where(
            PortalLink.cycle_id == cycle.id,
            PortalLink.origin == LinkOrigin.CLIENT_REQUEST,
            PortalLink.created_at >= since,
        )
    )
    if (recent or 0) >= CLIENT_REQUESTS_PER_DAY:
        raise LinkProblem(
            "too_many_requests",
            "Several new links were sent today. Please contact us directly.",
            429,
            False,
        )
    address = primary_address(db, client)
    if address is None:
        raise LinkProblem(
            "no_contact",
            "We have no email address to send a new link to. Please contact us.",
            409,
            False,
        )
    new_token, link = issue(
        db,
        settings,
        client=client,
        cycle=cycle,
        origin=LinkOrigin.CLIENT_REQUEST,
        created_by_id=None,
        requested_from_id=old.id,
    )
    email_link(
        db,
        settings,
        link=link,
        token=new_token,
        client=client,
        cycle=cycle,
        to=address,
        created_by_id=None,
    )
    return link, mask_address(address)


def status_of(link: PortalLink, now: datetime | None = None) -> str:
    """For the ticket page: waiting, opened, expired, replaced or withdrawn."""
    now = now or utcnow()
    if link.used_at is not None:
        if link.revoked_at is not None:
            return "ended"
        return "opened" if ensure_utc(link.expires_at) > now else "ended"
    if link.revoked_at is not None:
        return "replaced" if link.revoked_reason == "replaced" else "withdrawn"
    return "waiting" if ensure_utc(link.expires_at) > now else "expired"
