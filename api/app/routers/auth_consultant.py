"""Consultant sign-in: an emailed link plus a one-time code (BRD NFR-SE-02).

1. On the sign-in page the consultant gives their email. The page is shown a 6-digit code, and a
   link is emailed (if, and only if, the address belongs to an active consultant with a signed
   confidentiality agreement; the reply is the same either way, so nobody can test addresses).
2. Opening the link asks for the code. Both are needed: a link forwarded or read by someone else
   is no use without the screen that asked for it, and the code alone is no use without the link.

The link works once, for 15 minutes, and only the newest one works. Wrong codes count against
the consultant's account like wrong passwords (lockout) and against the address that sent them.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import challenges, throttle
from app.auth.deps import get_db
from app.auth.events import security_event
from app.config import Settings
from app.errors import ApiHTTPException
from app.mail.outbox import create
from app.mail.templates import render
from app.models import ChallengePurpose, EmailKind, LoginChallenge, Role, User
from app.routers.auth import SessionOut, _ip, _sign_in, _too_many
from app.timeutil import ensure_utc, format_ist, utcnow

router = APIRouter(prefix="/api/auth/consultant", tags=["auth"])

LINK_TTL = timedelta(minutes=15)
COOLDOWN = timedelta(seconds=60)  # one link a minute per consultant, at most
BAD = "That link or code is not right, or the link has expired. Ask for a new link."


class StartIn(BaseModel):
    email: EmailStr


class StartOut(BaseModel):
    code: str  # show this on screen; the emailed link asks for it
    minutes: int


class FinishIn(BaseModel):
    token: str = Field(min_length=10, max_length=100)
    code: str = Field(min_length=6, max_length=12)


def _code_hash(settings: Settings, code: str) -> str:
    key = settings.secret_key.get_secret_value().encode()
    return hmac.new(key, b"consultant-code:" + code.encode(), hashlib.sha256).hexdigest()


def _digits(code: str) -> str:
    return "".join(c for c in code if c.isdigit())


def _eligible(user: User | None) -> bool:
    return (
        user is not None
        and user.is_active
        and user.role is Role.CONSULTANT
        and user.agreement_signed_on is not None
    )


@router.post("/start")
def start(body: StartIn, request: Request, db: Session = Depends(get_db)) -> StartOut:  # noqa: B008
    settings: Settings = request.app.state.settings
    ip = _ip(request)
    now = utcnow()
    if throttle.ip_is_throttled(db, ip, now):
        raise _too_many(ip)
    code = f"{secrets.randbelow(10**6):06d}"
    user = db.scalar(select(User).where(User.email == str(body.email).lower()))
    recent = (
        db.scalar(
            select(LoginChallenge.created_at)
            .where(LoginChallenge.user_id == user.id, LoginChallenge.used_at.is_(None))
            .order_by(LoginChallenge.id.desc())
            .limit(1)
        )
        if user is not None
        else None
    )
    cooling = recent is not None and ensure_utc(recent) > now - COOLDOWN
    if user is not None and _eligible(user) and not throttle.is_locked(user, now) and not cooling:
        token = challenges.issue(
            db,
            user,
            ChallengePurpose.EMAIL_LINK,
            now,
            ttl=LINK_TTL,
            code_hash=_code_hash(settings, code),
        )
        content = render(
            "consultant_sign_in",
            {
                "firm_name": settings.firm_name,
                "recipient_name": user.full_name,
                "link": f"{settings.public_url}/consultant/sign-in#t={token}",
                "expires": format_ist(now + LINK_TTL, "%H:%M IST"),
            },
        )
        create(
            db, kind=EmailKind.CONSULTANT_SIGN_IN, to=[user.email], content=content, queue_now=True
        )
        security_event("consultant.link_sent", db=db, user_id=user.id, ip=ip)
    else:
        # The same answer, with a code that leads nowhere: the address is not revealed.
        throttle.record_attempt(db, ip, False, now)
        security_event("consultant.link_refused", user_id=user.id if user else None, ip=ip)
    db.commit()
    return StartOut(code=f"{code[:3]} {code[3:]}", minutes=int(LINK_TTL.total_seconds() // 60))


@router.post("/finish")
def finish(
    body: FinishIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> SessionOut:
    settings: Settings = request.app.state.settings
    ip = _ip(request)
    now = utcnow()
    if throttle.ip_is_throttled(db, ip, now):
        raise _too_many(ip)
    ch = challenges.load(db, body.token, ChallengePurpose.EMAIL_LINK, now)
    user = db.get(User, ch.user_id) if ch else None
    if ch is None or user is None or not _eligible(user) or throttle.is_locked(user, now):
        throttle.record_attempt(db, ip, False, now)
        security_event("consultant.link_rejected", user_id=user.id if user else None, ip=ip)
        db.commit()
        raise ApiHTTPException(401, BAD, code="bad_link")
    given = _digits(body.code)
    if ch.code_hash is None or not hmac.compare_digest(_code_hash(settings, given), ch.code_hash):
        ch.attempts += 1
        throttle.record_attempt(db, ip, False, now)
        locked = throttle.register_failure(user, now)
        security_event("consultant.code_failed", db=db, user_id=user.id, ip=ip)
        if locked:
            security_event("login.locked", db=db, user_id=user.id, ip=ip, via="consultant-code")
        db.commit()
        raise ApiHTTPException(401, BAD, code="bad_code")
    ch.used_at = now
    return _sign_in(db, user, request, response)
