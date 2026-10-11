from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Role, User, UserSession
from app.timeutil import utcnow

SESSION_LIFETIME = timedelta(hours=12)
# BRD NFR-SE-03: Staff and Administrator sessions end after 20 minutes without activity.
IDLE_TIMEOUT = timedelta(minutes=20)
IDLE_ROLES = frozenset({Role.ADMINISTRATOR, Role.STAFF})


@dataclass(frozen=True)
class Loaded:
    session: UserSession
    user: User
    idle: bool  # signed in, but has been inactive for too long


def cookie_name(settings: Settings) -> str:
    # The __Host- prefix makes browsers refuse the cookie unless it is Secure, Path=/ and has no Domain.
    return "__Host-ll_session" if settings.is_production else "ll_session"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User, ip: str) -> tuple[str, UserSession]:
    token = secrets.token_urlsafe(32)
    now = utcnow()
    row = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        ip=ip[:45],
        created_at=now,
        last_seen_at=now,
        expires_at=now + SESSION_LIFETIME,
    )
    db.add(row)
    db.flush()
    return token, row


def load_session(db: Session, token: str, now: datetime | None = None) -> Loaded | None:
    """The live session, or None if unknown, revoked, expired or the user is disabled."""
    now = now or utcnow()
    row = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        return None
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    idle = user.role in IDLE_ROLES and now - row.last_seen_at > IDLE_TIMEOUT
    return Loaded(row, user, idle)


def touch(db: Session, row: UserSession, now: datetime) -> None:
    """Record activity. Done on every request, so the 20 minutes are measured exactly."""
    row.last_seen_at = now
    db.flush()


def revoke_session(db: Session, row: UserSession) -> None:
    row.revoked_at = utcnow()
    db.flush()


def revoke_all_for_user(db: Session, user_id: int, *, except_session_id: int | None = None) -> int:
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    if except_session_id is not None:
        stmt = stmt.where(UserSession.id != except_session_id)
    result = db.execute(stmt)
    return int(result.rowcount)  # type: ignore[attr-defined]
