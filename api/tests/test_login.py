from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from app.auth import throttle
from app.main import create_app
from app.models import Role, User, UserSession
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from tests.conftest import make_settings
from tests.helpers import PASSWORD, db_session, login, make_user, sign_in

pytestmark = pytest.mark.requires_db

GENERIC = "Email or password is incorrect, or the account is temporarily locked."


def test_successful_login_returns_person_and_csrf_token_but_no_secrets(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in", Role.STAFF, name="Asha Rao")
    resp = login(web, "asha@firm.in")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "asha@firm.in"
    assert body["user"]["role"] == "staff"
    assert len(body["csrf_token"]) >= 32
    assert "password" not in resp.text.lower().replace("must_change_password", "")
    assert "argon2" not in resp.text


def test_session_cookie_is_hardened(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    header = login(web, "asha@firm.in").headers["set-cookie"]
    assert header.startswith("ll_session=")
    low = header.lower()
    assert "httponly" in low
    assert "samesite=lax" in low
    assert "path=/" in low
    assert "domain" not in low
    assert "max-age=43200" in low


def test_production_cookie_is_host_prefixed_and_secure(migrated_db: str) -> None:
    app = create_app(
        make_settings(env="production", database_url=migrated_db, allowed_hosts="app.example.test")
    )
    make_user(migrated_db, "asha@firm.in")
    with TestClient(app, base_url="https://app.example.test") as client:
        header = login(client, "asha@firm.in").headers["set-cookie"]
    assert header.startswith("__Host-ll_session=")
    assert "secure" in header.lower()


def test_only_a_hash_of_the_cookie_is_stored(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    login(web, "asha@firm.in")
    raw = web.cookies.get("ll_session")
    assert raw
    with db_session(migrated_db) as db:
        row = db.scalar(select(UserSession))
        assert row is not None
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert raw not in {row.token_hash, row.csrf_token}
        dump = db.execute(text("SELECT GROUP_CONCAT(token_hash) FROM sessions")).scalar()
        assert raw not in str(dump)


@pytest.mark.parametrize(
    "case", ["wrong_password", "unknown_email", "disabled", "consultant", "no_password_set"]
)
def test_every_kind_of_failure_looks_the_same(web: TestClient, migrated_db: str, case: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    make_user(migrated_db, "off@firm.in", active=False)
    make_user(migrated_db, "cons@firm.in", Role.CONSULTANT)
    make_user(migrated_db, "nopw@firm.in", password=False, role=Role.CONSULTANT)
    email, password = {
        "wrong_password": ("asha@firm.in", "not the password at all"),
        "unknown_email": ("ghost@firm.in", PASSWORD),
        "disabled": ("off@firm.in", PASSWORD),
        "consultant": ("cons@firm.in", PASSWORD),
        "no_password_set": ("nopw@firm.in", PASSWORD),
    }[case]
    resp = login(web, email, password)
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == GENERIC
    assert "set-cookie" not in resp.headers


def test_account_locks_after_five_wrong_passwords_even_for_the_right_one(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    for _ in range(5):
        assert login(web, "asha@firm.in", "wrong wrong wrong").status_code == 401
    resp = login(web, "asha@firm.in")  # correct password, but locked
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == GENERIC
    with db_session(migrated_db) as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.locked_until is not None


def test_lock_expires_and_success_resets_the_counter(
    web: TestClient, migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(migrated_db, "asha@firm.in")
    for _ in range(5):
        login(web, "asha@firm.in", "wrong wrong wrong")
    later = utcnow() + throttle.ACCOUNT_LOCK + timedelta(seconds=5)
    monkeypatch.setattr("app.routers.auth.utcnow", lambda: later)
    assert login(web, "asha@firm.in").status_code == 200
    with db_session(migrated_db) as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.failed_login_count == 0
        assert user.locked_until is None


def test_a_success_in_between_prevents_lockout(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    for _ in range(4):
        login(web, "asha@firm.in", "wrong wrong wrong")
    assert login(web, "asha@firm.in").status_code == 200
    for _ in range(4):
        login(web, "asha@firm.in", "wrong wrong wrong")
    assert login(web, "asha@firm.in").status_code == 200


def test_one_address_is_throttled_across_many_accounts(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    for i in range(throttle.IP_MAX_FAILURES):
        login(web, f"ghost{i}@firm.in", "wrong wrong wrong")
    resp = login(web, "asha@firm.in")  # valid credentials, but this address is throttled
    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "900"


def test_untrusted_origin_cannot_post_login(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    bad = web.post(
        "/api/auth/login",
        json={"email": "asha@firm.in", "password": PASSWORD},
        headers={"Origin": "https://evil.test"},
    )
    assert bad.status_code == 403
    assert bad.json()["error"]["code"] == "bad_origin"
    good = web.post(
        "/api/auth/login",
        json={"email": "asha@firm.in", "password": PASSWORD},
        headers={"Origin": "https://app.example.test"},
    )
    assert good.status_code == 200


def test_malformed_login_is_rejected_without_echoing_input(web: TestClient) -> None:
    resp = web.post("/api/auth/login", json={"email": "not-an-email", "password": "x"})
    assert resp.status_code == 422
    assert "not-an-email" not in resp.text


def test_me_requires_a_session_and_logout_ends_it(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    assert web.get("/api/auth/me").status_code == 401
    signed = sign_in(web, "asha@firm.in")
    me = web.get("/api/auth/me").json()
    assert me["csrf_token"] == signed.csrf
    assert web.post("/api/auth/logout", headers=signed.headers).status_code == 204
    assert web.get("/api/auth/me").status_code == 401


def test_state_changing_requests_need_the_csrf_token(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    signed = sign_in(web, "asha@firm.in")
    assert web.post("/api/auth/logout").status_code == 403
    assert web.post("/api/auth/logout", headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert web.get("/api/auth/me").status_code == 200  # still signed in
    assert web.post("/api/auth/logout", headers=signed.headers).status_code == 204


def test_logs_never_contain_the_password_or_raw_email(
    web: TestClient, migrated_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    from app.logging_conf import RedactingJsonFormatter

    make_user(migrated_db, "asha@firm.in")
    with caplog.at_level("INFO"):
        login(web, "asha@firm.in", "hunter2-hunter2-hunter2")
        login(web, "asha@firm.in")
    fmt = RedactingJsonFormatter()
    text_out = "\n".join(fmt.format(r) for r in caplog.records)
    assert "login.failed" in text_out
    assert "login.succeeded" in text_out
    assert "hunter2" not in text_out
    assert "asha@firm.in" not in text_out
