"""Two-step sign-in end to end (BRD NFR-SE-02)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pytest
from app.auth import challenges, mfa, throttle
from app.logging_conf import RedactingJsonFormatter
from app.models import LoginAttempt, LoginChallenge, RecoveryCode, Role, User, UserSession
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from tests.conftest import GOOD_SECRET
from tests.helpers import (
    PASSWORD,
    TEST_TOTP_SECRET,
    current_code,
    db_session,
    forget_last_code,
    login,
    make_user,
    sign_in,
)

pytestmark = pytest.mark.requires_db


def verify(web: TestClient, challenge: str, code: str) -> Any:
    return web.post("/api/auth/mfa/verify", json={"challenge": challenge, "code": code})


def start(web: TestClient, challenge: str) -> Any:
    return web.post("/api/auth/mfa/enrol/start", json={"challenge": challenge})


def confirm(web: TestClient, challenge: str, code: str) -> Any:
    return web.post("/api/auth/mfa/enrol/confirm", json={"challenge": challenge, "code": code})


def wrong_code() -> str:
    good = current_code()
    return "000000" if good != "000000" else "111111"


def new_challenge(web: TestClient, email: str) -> str:
    resp = login(web, email)
    assert resp.status_code == 200
    return str(resp.json()["challenge"])


def user_row(url: str, email: str) -> User:
    with db_session(url) as db:
        user = db.scalar(select(User).where(User.email == email))
        assert user is not None
        return user


# ---- enrolment -------------------------------------------------------------------------------


def test_first_sign_in_sets_up_an_authenticator_and_shows_recovery_codes_once(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR, two_step=False)
    resp = login(web, "boss@firm.in")
    first = resp.json()
    assert first["status"] == "mfa_enrolment_required"
    assert "set-cookie" not in resp.headers

    info = start(web, first["challenge"]).json()
    assert info["otpauth_uri"].startswith("otpauth://totp/Ledgerline:boss%40firm.in")
    assert info["qr"].startswith("data:image/svg+xml")
    secret = info["secret"].replace(" ", "")
    assert len(secret) == 32

    challenge = login(web, "boss@firm.in").json()[
        "challenge"
    ]  # a fresh login replaces the old challenge
    again = start(web, challenge).json()["secret"].replace(" ", "")
    assert again != secret  # new challenge, new secret
    assert (
        start(web, challenge).json()["secret"].replace(" ", "") == again
    )  # same challenge: stable

    done = confirm(web, challenge, mfa.totp_at(again, utcnow()))
    assert done.status_code == 200
    body = done.json()
    assert body["status"] == "signed_in"
    assert "ll_session=" in done.headers["set-cookie"]
    assert len(body["recovery_codes"]) == 10
    assert len(set(body["recovery_codes"])) == 10
    assert body["recovery_codes_left"] == 10
    assert body["user"]["mfa_enabled"] is True
    assert web.get("/api/auth/me").status_code == 200

    # Stored encrypted, never as the plain secret or plain recovery codes.
    row = user_row(migrated_db, "boss@firm.in")
    assert row.totp_secret_enc is not None
    assert again not in row.totp_secret_enc
    assert mfa.decrypt_secret(row.totp_secret_enc, GOOD_SECRET) == again
    with db_session(migrated_db) as db:
        hashes = set(db.scalars(select(RecoveryCode.code_hash)).all())
    assert len(hashes) == 10
    assert not hashes & set(body["recovery_codes"])

    # From now on the code is required.
    with TestClient(web.app) as other:
        assert login(other, "boss@firm.in").json()["status"] == "mfa_required"


def test_a_wrong_code_during_setup_gives_no_session_and_changes_nothing(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR, two_step=False)
    challenge = new_challenge(web, "boss@firm.in")
    start(web, challenge)
    resp = confirm(web, challenge, wrong_code())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "bad_code"
    assert "set-cookie" not in resp.headers
    assert user_row(migrated_db, "boss@firm.in").totp_enabled_at is None


def test_confirming_before_starting_is_refused(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR, two_step=False)
    resp = confirm(web, new_challenge(web, "boss@firm.in"), "123456")
    assert resp.status_code == 409


def test_a_challenge_is_single_use(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR, two_step=False)
    challenge = new_challenge(web, "boss@firm.in")
    secret = start(web, challenge).json()["secret"].replace(" ", "")
    assert confirm(web, challenge, mfa.totp_at(secret, utcnow())).status_code == 200
    reuse = confirm(web, challenge, mfa.totp_at(secret, utcnow() + timedelta(seconds=30)))
    assert reuse.status_code == 401
    assert reuse.json()["error"]["code"] == "challenge_expired"
    assert start(web, challenge).status_code == 401


def test_a_setup_challenge_cannot_be_used_to_verify_and_the_reverse(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "new@firm.in", Role.STAFF, two_step=False)
    make_user(migrated_db, "old@firm.in", Role.STAFF)
    setup = new_challenge(web, "new@firm.in")
    check = new_challenge(web, "old@firm.in")
    assert verify(web, setup, current_code()).status_code == 401  # setup token on the verify door
    assert start(web, check).status_code == 401  # verify token on the setup door
    assert confirm(web, check, current_code()).status_code == 401


# ---- verifying ---------------------------------------------------------------------------------


def test_wrong_code_gives_no_session(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    resp = verify(web, new_challenge(web, "asha@firm.in"), wrong_code())
    assert resp.status_code == 401
    assert resp.json()["error"]["message"] == "That code is not correct."
    assert "set-cookie" not in resp.headers
    with db_session(migrated_db) as db:
        assert db.scalar(select(func.count()).select_from(UserSession)) == 0


def test_a_challenge_dies_after_five_wrong_codes_even_for_the_right_one(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    challenge = new_challenge(web, "asha@firm.in")
    for _ in range(challenges.MAX_ATTEMPTS):
        assert verify(web, challenge, wrong_code()).status_code == 401
    forget_last_code(web, "asha@firm.in")
    late = verify(web, challenge, current_code())
    assert late.status_code == 401
    assert late.json()["error"]["code"] == "challenge_expired"


def test_wrong_codes_across_fresh_logins_still_lock_the_account(
    web: TestClient, migrated_db: str
) -> None:
    """Re-entering the password does not reset the guessing allowance."""
    make_user(migrated_db, "asha@firm.in")
    for _ in range(throttle.ACCOUNT_MAX_FAILURES):
        verify(web, new_challenge(web, "asha@firm.in"), wrong_code())
    assert user_row(migrated_db, "asha@firm.in").locked_until is not None
    assert login(web, "asha@firm.in").status_code == 401  # even with the right password


def test_a_correct_password_does_not_clear_failed_attempts_only_a_full_sign_in_does(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    for _ in range(3):
        login(web, "asha@firm.in", "wrong wrong wrong")
    new_challenge(web, "asha@firm.in")  # password right
    assert user_row(migrated_db, "asha@firm.in").failed_login_count == 3
    sign_in(web, "asha@firm.in")
    assert user_row(migrated_db, "asha@firm.in").failed_login_count == 0


def test_a_code_cannot_be_replayed(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    code = current_code()
    forget_last_code(web, "asha@firm.in")
    assert verify(web, new_challenge(web, "asha@firm.in"), code).status_code == 200
    with TestClient(web.app) as thief:
        replay = verify(thief, new_challenge(thief, "asha@firm.in"), code)
    assert replay.status_code == 401


def test_challenges_expire_after_five_minutes(
    web: TestClient, migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(migrated_db, "asha@firm.in")
    challenge = new_challenge(web, "asha@firm.in")
    later = utcnow() + challenges.CHALLENGE_TTL + timedelta(seconds=1)
    monkeypatch.setattr("app.routers.auth.utcnow", lambda: later)
    forget_last_code(web, "asha@firm.in")
    resp = verify(web, challenge, current_code(later))
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "challenge_expired"


def test_a_new_login_replaces_the_old_challenge(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    old = new_challenge(web, "asha@firm.in")
    new_challenge(web, "asha@firm.in")
    forget_last_code(web, "asha@firm.in")
    assert verify(web, old, current_code()).status_code == 401
    with db_session(migrated_db) as db:
        assert db.scalar(select(func.count()).select_from(LoginChallenge)) == 1


def test_disabling_between_password_and_code_stops_the_sign_in(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    challenge = new_challenge(web, "asha@firm.in")
    with db_session(migrated_db) as db:
        db.execute(update(User).values(is_active=False))
        db.commit()
    forget_last_code(web, "asha@firm.in")
    assert verify(web, challenge, current_code()).status_code == 401


def test_the_address_limit_covers_the_second_step(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    challenge = new_challenge(web, "asha@firm.in")
    now = utcnow()
    with db_session(migrated_db) as db:
        for _ in range(throttle.IP_MAX_FAILURES):
            db.add(LoginAttempt(ip="testclient", attempted_at=now, succeeded=False))
        db.commit()
    forget_last_code(web, "asha@firm.in")
    resp = verify(web, challenge, current_code())
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "throttled"


@pytest.mark.parametrize("junk", ["x" * 25, "a" * 80])
def test_garbage_challenges_are_a_clean_401(web: TestClient, junk: str) -> None:
    assert verify(web, junk, "123456").status_code == 401
    assert start(web, junk).status_code == 401
    assert confirm(web, junk, "123456").status_code == 401


def test_short_challenges_and_non_ascii_codes_are_clean_errors(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    assert verify(web, "short", "123456").status_code == 422
    challenge = new_challenge(web, "asha@firm.in")
    resp = verify(web, challenge, "\uff11\uff12\uff13\uff14\uff15\uff16")
    assert resp.status_code == 401  # a 401, not a server error
    assert verify(web, challenge, "").status_code == 422


# ---- recovery codes ----------------------------------------------------------------------------


def _with_recovery_codes(web: TestClient, url: str, email: str) -> list[str]:
    """Give an enrolled person ten recovery codes by signing in and regenerating them."""
    signed = sign_in(web, email)
    resp = web.post(
        "/api/auth/mfa/recovery-codes", json={"current_password": PASSWORD}, headers=signed.headers
    )
    assert resp.status_code == 200
    web.post("/api/auth/logout", headers=signed.headers)
    return list(resp.json()["recovery_codes"])


def test_a_recovery_code_signs_you_in_once(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    codes = _with_recovery_codes(web, migrated_db, "asha@firm.in")
    with TestClient(web.app) as c:
        ok = verify(c, new_challenge(c, "asha@firm.in"), codes[0].upper().replace("-", " "))
        assert ok.status_code == 200
        assert ok.json()["recovery_codes_left"] == 9
    with TestClient(web.app) as c:
        used_again = verify(c, new_challenge(c, "asha@firm.in"), codes[0])
        assert used_again.status_code == 401
        other = verify(c, new_challenge(c, "asha@firm.in"), codes[1])
        assert other.status_code == 200
        assert other.json()["recovery_codes_left"] == 8


def test_wrong_recovery_codes_count_as_failures(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "asha@firm.in")
    _with_recovery_codes(web, migrated_db, "asha@firm.in")
    for _ in range(throttle.ACCOUNT_MAX_FAILURES):
        verify(web, new_challenge(web, "asha@firm.in"), "abcde-fghjk")
    assert user_row(migrated_db, "asha@firm.in").locked_until is not None


def test_new_recovery_codes_replace_the_old_ones_and_need_the_password(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "asha@firm.in")
    old = _with_recovery_codes(web, migrated_db, "asha@firm.in")
    signed = sign_in(web, "asha@firm.in")
    assert web.get("/api/auth/me").json()["recovery_codes_left"] == 10

    denied = web.post(
        "/api/auth/mfa/recovery-codes",
        json={"current_password": "not the password!"},
        headers=signed.headers,
    )
    assert denied.status_code == 400
    assert (
        web.post("/api/auth/mfa/recovery-codes", json={"current_password": PASSWORD}).status_code
        == 403
    )  # no CSRF

    fresh = web.post(
        "/api/auth/mfa/recovery-codes", json={"current_password": PASSWORD}, headers=signed.headers
    ).json()["recovery_codes"]
    assert len(fresh) == 10
    assert not set(fresh) & set(old)
    web.post("/api/auth/logout", headers=signed.headers)
    with TestClient(web.app) as c:
        assert verify(c, new_challenge(c, "asha@firm.in"), old[0]).status_code == 401
        assert verify(c, new_challenge(c, "asha@firm.in"), fresh[0]).status_code == 200


# ---- password changes and resets leave two-step alone ---------------------------------------


def test_changing_or_resetting_a_password_does_not_remove_two_step(
    web: TestClient, migrated_db: str
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    staff_id = make_user(migrated_db, "asha@firm.in")
    with TestClient(web.app) as c:
        admin = sign_in(c, "boss@firm.in")
        assert (
            c.post(f"/api/admin/users/{staff_id}/reset-password", headers=admin.headers).status_code
            == 200
        )
    row = user_row(migrated_db, "asha@firm.in")
    assert row.totp_enabled_at is not None
    assert row.totp_secret_enc is not None


# ---- administrator reset -----------------------------------------------------------------------


def test_administrator_can_reset_someones_two_step(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    staff_id = make_user(migrated_db, "asha@firm.in")
    codes = _with_recovery_codes(web, migrated_db, "asha@firm.in")
    with TestClient(web.app) as asha:
        sign_in(asha, "asha@firm.in")
        admin = sign_in(web, "boss@firm.in")
        resp = web.post(f"/api/admin/users/{staff_id}/reset-mfa", headers=admin.headers)
        assert resp.status_code == 200
        assert resp.json()["mfa_enabled"] is False
        assert asha.get("/api/auth/me").status_code == 401  # sessions ended
    with db_session(migrated_db) as db:
        assert db.scalar(select(func.count()).select_from(RecoveryCode)) == 0
    with TestClient(web.app) as c:
        assert login(c, "asha@firm.in").json()["status"] == "mfa_enrolment_required"
        # old recovery codes are gone, and cannot be used at the setup door either
        assert verify(c, new_challenge(c, "asha@firm.in"), codes[0]).status_code == 401


def test_you_cannot_reset_your_own_and_consultants_have_none(
    web: TestClient, migrated_db: str
) -> None:
    boss = make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    cons = make_user(migrated_db, "cons@firm.in", Role.CONSULTANT)
    admin = sign_in(web, "boss@firm.in")
    assert web.post(f"/api/admin/users/{boss}/reset-mfa", headers=admin.headers).status_code == 409
    assert web.post(f"/api/admin/users/{cons}/reset-mfa", headers=admin.headers).status_code == 409
    assert web.get("/api/admin/users").json()[0]["mfa_enabled"] is True


# ---- the second step is never skipped, and never logged ------------------------------------


def test_no_route_hands_out_a_session_except_after_the_second_step() -> None:
    """Structural guarantee: sessions are created in exactly one place, reached only from the
    second-step endpoints (and from login for roles that need no second step, of which there are
    none today)."""
    import ast
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    callers: dict[str, set[str]] = {}
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                    if name in {"create_session", "_sign_in"}:
                        callers.setdefault(name, set()).add(f"{path.name}:{fn.name}")
    assert callers["create_session"] == {"auth.py:_sign_in"}
    assert callers["_sign_in"] == {
        "auth.py:login",  # only for roles with needs_second_step() False
        "auth.py:mfa_verify",
        "auth.py:mfa_enrol_confirm",
    }


def test_staff_and_administrators_always_need_the_second_step() -> None:
    from app.routers.auth import needs_second_step

    assert needs_second_step(Role.STAFF) is True
    assert needs_second_step(Role.ADMINISTRATOR) is True


def test_codes_and_secrets_never_reach_the_logs(
    web: TestClient, migrated_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR, two_step=False)
    with caplog.at_level(logging.INFO):
        challenge = new_challenge(web, "boss@firm.in")
        secret = start(web, challenge).json()["secret"].replace(" ", "")
        code = mfa.totp_at(secret, utcnow())
        confirm(web, challenge, wrong_code())
        done = confirm(web, challenge, code).json()
    text = "\n".join(RedactingJsonFormatter().format(r) for r in caplog.records)
    assert "mfa.failed" in text
    assert "mfa.enrolled" in text
    assert secret not in text
    assert code not in text or code == wrong_code()
    for recovery in done["recovery_codes"]:
        assert recovery not in text
    assert TEST_TOTP_SECRET not in text
