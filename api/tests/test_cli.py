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
    assert resp.json()["user"]["role"] == "administrator"


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
