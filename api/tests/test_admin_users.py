from __future__ import annotations

from datetime import date, timedelta

import pytest
from app.models import Role, User
from app.timeutil import today_ist
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from tests.helpers import (
    PASSWORD,
    db_session,
    login,
    make_user,
    sign_in,
    sign_in_directly,
)

pytestmark = pytest.mark.requires_db

ADMIN_ROUTES = [
    ("GET", "/api/admin/users", None),
    ("POST", "/api/admin/users", {"email": "n@firm.in", "full_name": "New", "role": "staff"}),
    ("POST", "/api/admin/users/1/disable", None),
    ("POST", "/api/admin/users/1/enable", None),
    ("POST", "/api/admin/users/1/reset-password", None),
]


def _call(signed_client: TestClient, method: str, path: str, body: object, headers: dict[str, str]):  # type: ignore[no-untyped-def]
    return signed_client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ROUTES)
def test_only_the_administrator_may_manage_people(
    web: TestClient, migrated_db: str, method: str, path: str, body: object
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    staff = make_user(migrated_db, "asha@firm.in", Role.STAFF)
    consultant = make_user(migrated_db, "cons@firm.in", Role.CONSULTANT)

    assert _call(web, method, path, body, {}).status_code == 401  # nobody signed in

    with TestClient(web.app) as c:
        s = sign_in_directly(migrated_db, c, staff)
        assert _call(c, method, path, body, s.headers).status_code == 403
    with TestClient(web.app) as c:
        s = sign_in_directly(migrated_db, c, consultant)
        assert _call(c, method, path, body, s.headers).status_code == 403
    with TestClient(web.app) as c:
        admin = sign_in(c, "boss@firm.in")
        assert _call(c, method, path, body, admin.headers).status_code != 403


def test_admin_posts_need_the_csrf_token(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    sign_in(web, "boss@firm.in")
    resp = web.post(
        "/api/admin/users", json={"email": "n@firm.in", "full_name": "New", "role": "staff"}
    )
    assert resp.status_code == 403


def test_create_staff_returns_a_one_time_password_and_forces_a_change(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    admin = sign_in(web, "boss@firm.in")
    resp = web.post(
        "/api/admin/users",
        json={"email": "Asha.Rao@Firm.in", "full_name": "  Asha   Rao ", "role": "staff"},
        headers=admin.headers,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["person"]["email"] == "asha.rao@firm.in"
    assert body["person"]["full_name"] == "Asha Rao"
    assert body["person"]["must_change_password"] is True
    assert "password_hash" not in resp.text
    temp = body["temporary_password"]
    assert temp
    with TestClient(web.app) as c:
        signed = login(c, "asha.rao@firm.in", temp)
        assert signed.status_code == 200
        assert signed.json()["user"]["must_change_password"] is True


def test_duplicate_email_is_refused_ignoring_case(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    make_user(migrated_db, "asha@firm.in")
    admin = sign_in(web, "boss@firm.in")
    resp = web.post(
        "/api/admin/users",
        json={"email": "ASHA@firm.in", "full_name": "Other", "role": "staff"},
        headers=admin.headers,
    )
    assert resp.status_code == 409


def test_consultants_need_a_valid_agreement_date_and_get_no_password(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    admin = sign_in(web, "boss@firm.in")
    base = {"email": "c@ca-firm.in", "full_name": "C Associates", "role": "consultant"}

    missing = web.post("/api/admin/users", json=base, headers=admin.headers)
    assert missing.status_code == 422
    assert "confidentiality agreement" in missing.json()["error"]["message"]

    future = web.post(
        "/api/admin/users",
        json={**base, "agreement_signed_on": (today_ist() + timedelta(days=1)).isoformat()},
        headers=admin.headers,
    )
    assert future.status_code == 422

    ok = web.post(
        "/api/admin/users",
        json={**base, "agreement_signed_on": "2026-03-01"},
        headers=admin.headers,
    )
    assert ok.status_code == 201
    assert ok.json()["temporary_password"] is None
    assert ok.json()["person"]["agreement_signed_on"] == "2026-03-01"
    with TestClient(web.app) as c:
        assert login(c, "c@ca-firm.in", PASSWORD).status_code == 401  # emailed link comes in C21


def test_only_consultants_carry_an_agreement_date(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    admin = sign_in(web, "boss@firm.in")
    resp = web.post(
        "/api/admin/users",
        json={
            "email": "n@firm.in",
            "full_name": "New",
            "role": "staff",
            "agreement_signed_on": "2026-03-01",
        },
        headers=admin.headers,
    )
    assert resp.status_code == 422


def test_the_database_itself_refuses_incomplete_accounts(migrated_db: str) -> None:
    with db_session(migrated_db) as db:
        db.add(User(email="c@x.in", full_name="C", role=Role.CONSULTANT, password_hash=None))
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        db.add(User(email="s@x.in", full_name="S", role=Role.STAFF, password_hash=None))
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        db.add(
            User(
                email="ok@x.in",
                full_name="C",
                role=Role.CONSULTANT,
                password_hash=None,
                agreement_signed_on=date(2026, 1, 1),
            )
        )
        db.commit()


def test_disabling_ends_sessions_at_once(web: TestClient, migrated_db: str) -> None:
    """BRD FR-AC-02: a disabled person's next request fails."""
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    staff_id = make_user(migrated_db, "asha@firm.in")
    with TestClient(web.app) as staff_browser:
        sign_in(staff_browser, "asha@firm.in")
        assert staff_browser.get("/api/auth/me").status_code == 200

        admin = sign_in(web, "boss@firm.in")
        resp = web.post(f"/api/admin/users/{staff_id}/disable", headers=admin.headers)
        assert resp.status_code == 200
        assert resp.json()["is_active"] is False

        assert staff_browser.get("/api/auth/me").status_code == 401  # very next request
        assert login(staff_browser, "asha@firm.in").status_code == 401  # and cannot sign back in


def test_enable_restores_access_and_clears_a_lock(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    staff_id = make_user(migrated_db, "asha@firm.in")
    admin = sign_in(web, "boss@firm.in")
    with TestClient(web.app) as c:
        for _ in range(5):
            login(c, "asha@firm.in", "wrong wrong wrong")
        assert login(c, "asha@firm.in").status_code == 401  # locked
        web.post(f"/api/admin/users/{staff_id}/disable", headers=admin.headers)
        web.post(f"/api/admin/users/{staff_id}/enable", headers=admin.headers)
        assert login(c, "asha@firm.in").status_code == 200


def test_you_cannot_disable_yourself(web: TestClient, migrated_db: str) -> None:
    boss = make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    admin = sign_in(web, "boss@firm.in")
    resp = web.post(f"/api/admin/users/{boss}/disable", headers=admin.headers)
    assert resp.status_code == 409
    assert web.get("/api/auth/me").status_code == 200


def test_administrators_can_disable_each_other_but_one_always_remains(
    web: TestClient, migrated_db: str
) -> None:
    boss = make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    other = make_user(migrated_db, "boss2@firm.in", Role.ADMINISTRATOR)
    with TestClient(web.app) as c2:
        a = sign_in(web, "boss@firm.in")
        b = sign_in(c2, "boss2@firm.in")
        assert web.post(f"/api/admin/users/{other}/disable", headers=a.headers).status_code == 200
        # The second administrator's session ended with the disabling, so they cannot reply in kind.
        assert c2.post(f"/api/admin/users/{boss}/disable", headers=b.headers).status_code == 401
    with db_session(migrated_db) as db:
        active = db.scalars(
            select(User.id).where(User.role == Role.ADMINISTRATOR, User.is_active.is_(True))
        ).all()
        assert list(active) == [boss]


def test_reset_password_gives_a_new_temporary_one_and_ends_sessions(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    staff_id = make_user(migrated_db, "asha@firm.in")
    admin = sign_in(web, "boss@firm.in")
    with TestClient(web.app) as c:
        sign_in(c, "asha@firm.in")
        resp = web.post(f"/api/admin/users/{staff_id}/reset-password", headers=admin.headers)
        assert resp.status_code == 200
        temp = resp.json()["temporary_password"]
        assert c.get("/api/auth/me").status_code == 401
        assert login(c, "asha@firm.in", PASSWORD).status_code == 401
        again = login(c, "asha@firm.in", temp)
        assert again.status_code == 200
        assert again.json()["user"]["must_change_password"] is True


def test_consultants_have_no_password_to_reset(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    cons = make_user(migrated_db, "cons@firm.in", Role.CONSULTANT)
    admin = sign_in(web, "boss@firm.in")
    assert (
        web.post(f"/api/admin/users/{cons}/reset-password", headers=admin.headers).status_code
        == 409
    )


def test_list_is_sorted_and_never_includes_secrets(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR, name="Zed Boss")
    make_user(migrated_db, "asha@firm.in", name="Asha Rao")
    admin = sign_in(web, "boss@firm.in")
    resp = web.get("/api/admin/users", headers=admin.headers)
    assert resp.status_code == 200
    assert [p["full_name"] for p in resp.json()] == ["Asha Rao", "Zed Boss"]
    for secret in ("password_hash", "argon2", "csrf", "token"):
        assert secret not in resp.text


def test_unknown_person_is_404(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    admin = sign_in(web, "boss@firm.in")
    assert web.post("/api/admin/users/9999/disable", headers=admin.headers).status_code == 404
