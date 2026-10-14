"""Login challenges: what a correct password earns while the second step is still to do."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import ChallengePurpose, LoginChallenge, User

CHALLENGE_TTL = timedelta(minutes=5)
MAX_ATTEMPTS = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue(
    db: Session,
    user: User,
    purpose: ChallengePurpose,
    now: datetime,
    *,
    ttl: timedelta = CHALLENGE_TTL,
    code_hash: str | None = None,
) -> str:
    """Create a challenge, replacing any unused one for this person (so attempts cannot pile up)."""
    db.execute(
        delete(LoginChallenge).where(
            LoginChallenge.user_id == user.id, LoginChallenge.used_at.is_(None)
        )
    )
    db.execute(delete(LoginChallenge).where(LoginChallenge.expires_at < now - timedelta(hours=1)))
    token = secrets.token_urlsafe(32)
    db.add(
        LoginChallenge(
            user_id=user.id,
            token_hash=_hash(token),
            purpose=purpose,
            created_at=now,
            expires_at=now + ttl,
            code_hash=code_hash,
        )
    )
    db.flush()
    return token


def load(
    db: Session, token: str, purpose: ChallengePurpose, now: datetime
) -> LoginChallenge | None:
    row = db.scalar(
        select(LoginChallenge)
        .where(LoginChallenge.token_hash == _hash(token))
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or row.purpose is not purpose
        or row.used_at is not None
        or row.expires_at <= now
        or row.attempts >= MAX_ATTEMPTS
    ):
        return None
    return row
