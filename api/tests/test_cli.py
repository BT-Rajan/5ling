from __future__ import annotations

import io

import pytest
from app.cli import main
from app.config import get_settings
from app.models import Role, User
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import GOOD_SECRET
from tests.helpers import db_session, login

pytestmark = pytest.mark.requires_db


@pytest.fixture
def cli_env(migrated_db: str, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("LL_DATABASE_URL", migrated_db)
    monkeypatch.setenv("LL_SECRET_KEY", GOOD_SECRET)
    get_settings.cache_clear()
    yield migrated_db
    get_settings.cache_clear()


def _run(monkeypatch: pytest.MonkeyPatch, password: str, *extra: str) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    args = ["create-admin", "--name", "The Boss", "--password-stdin", *extra]
    return main(args)


def test_creates_a_working_administrator(
    cli_env: str, web: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _run(monkeypatch, "a long passphrase here", "--email", "Boss@Firm.in") == 0
    with db_session(cli_env) as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.email == "boss@firm.in"
        assert user.role is Role.ADMINISTRATOR
        assert user.must_change_password is False
        assert user.password_hash is not None
        assert user.password_hash.startswith("$argon2id$")
    resp = login(web, "boss@firm.in", "a long passphrase here")
    assert resp.status_code == 200
    # A new Administrator has no authenticator yet, so the next step is to set one up.
    assert resp.json()["status"] == "mfa_enrolment_required"


def test_refuses_weak_passwords_duplicates_and_bad_emails(
    cli_env: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(monkeypatch, "short", "--email", "boss@firm.in") == 1
    assert "at least 12" in capsys.readouterr().err
    assert _run(monkeypatch, "a long passphrase here", "--email", "not-an-email") == 1
    assert _run(monkeypatch, "a long passphrase here", "--email", "boss@firm.in") == 0
    assert _run(monkeypatch, "another long passphrase", "--email", "BOSS@firm.in") == 1
    assert "already exists" in capsys.readouterr().err
    with db_session(cli_env) as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_reset_mfa_clears_two_step_and_ends_sessions(
    cli_env: str, web: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.models import RecoveryCode, UserSession
    from sqlalchemy import func

    from tests.helpers import make_user, sign_in

    make_user(cli_env, "boss@firm.in", Role.ADMINISTRATOR)
    sign_in(web, "boss@firm.in")
    assert web.get("/api/auth/me").status_code == 200

    assert main(["reset-mfa", "--email", "BOSS@firm.in"]) == 0
    assert "will set it up again" in capsys.readouterr().out

    assert web.get("/api/auth/me").status_code == 401  # session ended
    with db_session(cli_env) as db:
        user = db.scalar(select(User))
        assert user is not None
        assert user.totp_secret_enc is None
        assert user.totp_enabled_at is None
        assert user.totp_last_step is None
        assert db.scalar(select(func.count()).select_from(RecoveryCode)) == 0
        live = db.scalar(
            select(func.count()).select_from(UserSession).where(UserSession.revoked_at.is_(None))
        )
        assert live == 0
    assert login(web, "boss@firm.in").json()["status"] == "mfa_enrolment_required"


def test_reset_mfa_refuses_unknown_people_and_consultants(
    cli_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from tests.helpers import make_user

    make_user(cli_env, "cons@firm.in", Role.CONSULTANT)
    assert main(["reset-mfa", "--email", "ghost@firm.in"]) == 1
    assert "No one has that email" in capsys.readouterr().err
    assert main(["reset-mfa", "--email", "cons@firm.in"]) == 1
    assert "do not use two-step" in capsys.readouterr().err
