"""BRD NFR-SE-03: Staff and Administrator sessions end after 20 minutes without activity (the
default; an Administrator can change it, C29)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.models import RULE_LIMITS, Role, UserSession
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from tests.helpers import db_session, make_user, sign_in, sign_in_directly

pytestmark = pytest.mark.requires_db


def idle_for(url: str, seconds: float) -> None:
    with db_session(url) as db:
        db.execute(update(UserSession).values(last_seen_at=utcnow() - timedelta(seconds=seconds)))
        db.commit()


def test_the_limit_is_twenty_minutes_by_default() -> None:
    assert RULE_LIMITS["idle_timeout_minutes"][0] == 20


@pytest.mark.parametrize("role", [Role.STAFF, Role.ADMINISTRATOR])
def test_inactive_sessions_end_with_a_clear_message(
    web: TestClient, migrated_db: str, role: Role
) -> None:
    make_user(migrated_db, "someone@firm.in", role)
    sign_in(web, "someone@firm.in")
    idle_for(migrated_db, 20 * 60 + 5)
    resp = web.get("/api/auth/me")
    assert resp.status_code == 401
    error = resp.json()["error"]
    assert error["code"] == "session_idle"
    assert "20 minutes" in error["message"]
    # and it stays ended
    assert web.get("/api/auth/me").status_code == 401
    with db_session(migrated_db) as db:
        assert db.scalar(select(UserSession.revoked_at)) is not None


def test_just_inside_the_limit_is_still_fine_and_counts_as_activity(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    sign_in(web, "asha@firm.in")
    for _ in range(3):  # each request starts a fresh 20 minutes
        idle_for(migrated_db, 19 * 60 + 55)
        assert web.get("/api/auth/me").status_code == 200
    with db_session(migrated_db) as db:
        last_seen = db.scalar(select(UserSession.last_seen_at))
    assert last_seen is not None
    assert utcnow() - last_seen < timedelta(seconds=10)


def test_changes_after_a_long_gap_are_refused_not_just_reads(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    signed = sign_in(web, "asha@firm.in")
    idle_for(migrated_db, 25 * 60)
    resp = web.post("/api/auth/logout", headers=signed.headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "session_idle"


def test_the_twelve_hour_limit_still_applies_however_active_you_are(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    sign_in(web, "asha@firm.in")
    with db_session(migrated_db) as db:
        db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    resp = web.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthenticated"


def test_consultant_sessions_follow_their_own_rules_not_this_one(
    web: TestClient, migrated_db: str
) -> None:
    """Consultants sign in by emailed link in chunk C21, which sets their own limits."""
    cons = make_user(migrated_db, "cons@firm.in", Role.CONSULTANT)
    sign_in_directly(migrated_db, web, cons)
    idle_for(migrated_db, 5 * 3600)
    assert web.get("/api/auth/me").status_code == 200
