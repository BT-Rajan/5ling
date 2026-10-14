"""The client portal (BRD FR-AL-02, NFR-US-01): reached by a private link, not an account.

Nothing here works without a link that is still good, or the portal session it started, and a
session only ever shows its own client's one cycle. Refusals for known links are recorded in the
audit log; unknown tokens are only logged (recording every guess would let anyone fill the log).
"""

from __future__ import annotations

import hmac
import logging
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import get_db
from app.config import Settings
from app.cycles.service import ticket_code
from app.cycles.stages import STAGE_LABEL
from app.errors import ApiHTTPException
from app.models import Client, Cycle, Filing, PortalLink, Ticket
from app.portal.links import (
    LinkProblem,
    cookie_name,
    csrf_for,
    end_session,
    load_session,
    open_link,
    refusal_for,
    request_new,
)
from app.timeutil import ensure_utc, utcnow

router = APIRouter(prefix="/api/portal", tags=["client portal"])
log = logging.getLogger("ledgerline.portal")

CSRF_HEADER = "x-csrf-token"
Token = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class TokenIn(BaseModel):
    token: Token


class PortalOut(BaseModel):
    client_name: str
    client_id: int
    ticket: str
    filing_code: str
    filing_name: str
    period_label: str
    due_date: date
    stage_label: str
    expires_at: datetime
    csrf_token: str


class SentOut(BaseModel):
    sent_to: str  # masked


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _problem(exc: LinkProblem) -> ApiHTTPException:
    return ApiHTTPException(
        exc.status, str(exc), code=exc.code, extra={"can_request_new": exc.can_request_new}
    )


def _out(
    db: Session, settings: Settings, link: PortalLink, client: Client, cycle: Cycle, session: str
) -> PortalOut:
    ticket = db.get(Ticket, cycle.ticket_id)
    filing = db.get(Filing, cycle.filing_id)
    if ticket is None or filing is None:  # foreign keys prevent it
        raise RuntimeError("cycle refers to missing rows")
    return PortalOut(
        client_name=client.legal_name,
        client_id=client.client_id,
        ticket=ticket_code(ticket.number),
        filing_code=filing.code,
        filing_name=filing.name,
        period_label=cycle.period_label,
        due_date=cycle.due_date,
        stage_label=STAGE_LABEL[cycle.stage],
        expires_at=link.expires_at,
        csrf_token=csrf_for(settings, session),
    )


def _refused(db: Session, token: str, exc: LinkProblem) -> ApiHTTPException:
    db.rollback()
    known = refusal_for(db, token)
    if known is None:
        log.info("portal link not recognised")
    else:
        link, problem = known
        audit(
            db,
            "portal.link_refused",
            actor_role="client",
            target=("portal_link", link.id),
            details={"reason": problem.code},
        )
        db.commit()
    return _problem(exc)


@router.post("/open")
def open_portal(
    body: TokenIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> PortalOut:
    """Use the link (once) and start a portal session for its one cycle."""
    settings = _settings(request)
    try:
        opened = open_link(db, body.token)
    except LinkProblem as exc:
        raise _refused(db, body.token, exc) from None
    audit(
        db,
        "portal.link_opened",
        actor_role="client",
        target=("portal_link", opened.link.id),
        details={"client_id": opened.client.client_id, "cycle": opened.cycle.id},
    )
    db.commit()
    seconds = int((ensure_utc(opened.link.expires_at) - utcnow()).total_seconds())
    response.set_cookie(
        cookie_name(settings),
        opened.session_token,
        max_age=max(seconds, 1),
        httponly=True,
        secure=settings.is_production,
        samesite="strict",
        path="/",
    )
    return _out(db, settings, opened.link, opened.client, opened.cycle, opened.session_token)


@router.get("/me")
def portal_me(request: Request, db: Session = Depends(get_db)) -> PortalOut:  # noqa: B008
    """The cycle this portal session is for."""
    settings = _settings(request)
    token = request.cookies.get(cookie_name(settings))
    try:
        link, client, cycle = load_session(db, token)
    except LinkProblem as exc:
        raise _problem(exc) from None
    link.last_seen_at = utcnow()
    db.commit()
    return _out(db, settings, link, client, cycle, token or "")


@router.post("/logout", status_code=204)
def portal_logout(request: Request, db: Session = Depends(get_db)) -> Response:  # noqa: B008
    """End the session on this device. The link cannot be opened again; a new one can be asked for."""
    settings = _settings(request)
    token = request.cookies.get(cookie_name(settings))
    try:
        link, _, _ = load_session(db, token)
    except LinkProblem:
        link = None
    if link is not None:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), csrf_for(settings, token or "").encode()):
            raise ApiHTTPException(403, "This request could not be verified.", code="csrf_rejected")
        end_session(link)
        audit(db, "portal.signed_out", actor_role="client", target=("portal_link", link.id))
        db.commit()
    response = Response(status_code=204)
    response.delete_cookie(cookie_name(settings), path="/")
    return response


@router.post("/request-link", status_code=202)
def request_link(
    body: TokenIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> SentOut:
    """From a used, expired or replaced link: email a new one to the contact address on file."""
    try:
        link, sent_to = request_new(db, _settings(request), body.token)
    except LinkProblem as exc:
        db.rollback()
        raise _problem(exc) from None
    audit(
        db,
        "portal.link_requested",
        actor_role="client",
        target=("portal_link", link.id),
        details={"replaces": link.requested_from_id},
    )
    db.commit()
    return SentOut(sent_to=sent_to)
