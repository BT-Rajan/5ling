from __future__ import annotations

from datetime import timedelta

import pytest
from app.models import Role, UserSession
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.conftest import make_settings
from tests.helpers import PASSWORD, db_session, login, make_user, sign_in

pytestmark = pytest.mark.requires_db


def test_expired_session_is_refused(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    sign_in(web, "asha@firm.in")
    with db_session(migrated_db) as db:
        db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert web.get("/api/auth/me").status_code == 401


def test_revoked_session_is_refused(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    sign_in(web, "asha@firm.in")
    with db_session(migrated_db) as db:
        db.execute(update(UserSession).values(revoked_at=utcnow()))
        db.commit()
    assert web.get("/api/auth/me").status_code == 401


def test_a_tampered_cookie_is_refused(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    sign_in(web, "asha@firm.in")
    web.cookies.set("ll_session", "x" * 43)
    assert web.get("/api/auth/me").status_code == 401


def test_password_change_ends_other_sessions_but_keeps_this_one(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    mine = sign_in(web, "asha@firm.in")
    with TestClient(web.app) as other_browser:
        sign_in(other_browser, "asha@firm.in")
        resp = web.post(
            "/api/auth/change-password",
            json={"current_password": PASSWORD, "new_password": "a brand new long passphrase"},
            headers=mine.headers,
        )
        assert resp.status_code == 204
        assert other_browser.get("/api/auth/me").status_code == 401
    assert web.get("/api/auth/me").status_code == 200
    web.post("/api/auth/logout", headers=mine.headers)
    assert login(web, "asha@firm.in", PASSWORD).status_code == 401
    assert login(web, "asha@firm.in", "a brand new long passphrase").status_code == 200


@pytest.mark.parametrize(
    ("new_password", "fragment"),
    [
        ("short", "at least 12"),
        (PASSWORD, "not used just now"),
        ("asha-and-friends-1", "email name"),
    ],
)
def test_change_password_rejects_weak_choices(
    web: TestClient, migrated_db: str, new_password: str, fragment: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    mine = sign_in(web, "asha@firm.in")
    resp = web.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": new_password},
        headers=mine.headers,
    )
    assert resp.status_code == 422
    assert fragment in resp.json()["error"]["message"]


def test_change_password_needs_the_current_password_and_counts_toward_lockout(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    mine = sign_in(web, "asha@firm.in")
    for _ in range(5):
        resp = web.post(
            "/api/auth/change-password",
            json={
                "current_password": "wrong wrong wrong",
                "new_password": "a brand new long passphrase",
            },
            headers=mine.headers,
        )
        assert resp.status_code == 400
    web.post("/api/auth/logout", headers=mine.headers)
    assert login(web, "asha@firm.in").status_code == 401  # locked by the guessing above


def test_temporary_password_must_be_changed_before_anything_else(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in", must_change=True)
    mine = sign_in(web, "asha@firm.in")
    assert web.get("/api/auth/me").json()["user"]["must_change_password"] is True
    blocked = web.get("/api/admin/users")
    assert blocked.status_code == 403
    assert "Change your password" in blocked.json()["error"]["message"]
    done = web.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "a brand new long passphrase"},
        headers=mine.headers,
    )
    assert done.status_code == 204
    assert web.get("/api/auth/me").json()["user"]["must_change_password"] is False


def test_role_changes_take_effect_on_the_next_request(web: TestClient, migrated_db: str) -> None:
    from app.models import User
    from sqlalchemy import update as upd

    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    sign_in(web, "boss@firm.in")
    assert web.get("/api/admin/users").status_code == 200
    with db_session(migrated_db) as db:
        db.execute(upd(User).values(role=Role.STAFF))
        db.commit()
    assert web.get("/api/admin/users").status_code == 403


def test_settings_sessions_are_independent_per_browser(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    make_user(migrated_db, "ravi@firm.in")
    with TestClient(web.app) as other:
        a = sign_in(web, "asha@firm.in")
        b = sign_in(other, "ravi@firm.in")
        assert web.get("/api/auth/me").json()["user"]["email"] == "asha@firm.in"
        assert other.get("/api/auth/me").json()["user"]["email"] == "ravi@firm.in"
        # one person's CSRF token is useless in the other's session
        assert other.post("/api/auth/logout", headers=a.headers).status_code == 403
        assert other.post("/api/auth/logout", headers=b.headers).status_code == 204


def test_make_settings_helper_is_isolated_from_the_real_env() -> None:
    assert make_settings().env == "test"
