"""Slow down password guessing: per-account lockout and per-address limit."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models import LoginAttempt, User

ACCOUNT_MAX_FAILURES = 5
ACCOUNT_LOCK = timedelta(minutes=15)
IP_MAX_FAILURES = 20
IP_WINDOW = timedelta(minutes=15)
KEEP_ATTEMPTS = timedelta(hours=24)


def is_locked(user: User, now: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > now


def register_failure(user: User, now: datetime) -> bool:
    """Count a wrong password. Returns True if this failure locked the account."""
    user.failed_login_count += 1
    if user.failed_login_count >= ACCOUNT_MAX_FAILURES:
        user.locked_until = now + ACCOUNT_LOCK
        return True
    return False


def register_success(user: User) -> None:
    user.failed_login_count = 0
    user.locked_until = None


def ip_is_throttled(db: Session, ip: str, now: datetime) -> bool:
    failures = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.ip == ip,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.attempted_at > now - IP_WINDOW,
        )
    )
    return (failures or 0) >= IP_MAX_FAILURES


def record_attempt(db: Session, ip: str, succeeded: bool, now: datetime) -> None:
    db.add(LoginAttempt(ip=ip[:45], attempted_at=now, succeeded=succeeded))
    db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < now - KEEP_ATTEMPTS))
