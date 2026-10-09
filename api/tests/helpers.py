"""Test helpers: create people and sessions directly, so each test states only what it needs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.auth.passwords import hash_password
from app.auth.sessions import cookie_name, create_session
from app.db import make_engine, make_session_factory
from app.models import Role, User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

PASSWORD = "correct horse battery"
_HASH = hash_password(PASSWORD)  # hashed once; Argon2 is deliberately slow


def db_session(url: str) -> Session:
    return make_session_factory(make_engine(url))()


def make_user(
    url: str,
    email: str,
    role: Role = Role.STAFF,
    *,
    password: bool = True,
    active: bool = True,
    must_change: bool = False,
    name: str = "Test Person",
    **extra: Any,
) -> int:
    if role is Role.CONSULTANT:
        extra.setdefault("agreement_signed_on", date(2026, 1, 15))
        password = False
    with db_session(url) as db:
        user = User(
            email=email,
            full_name=name,
            role=role,
            password_hash=_HASH if password else None,
            is_active=active,
            must_change_password=must_change,
            **extra,
        )
        db.add(user)
        db.commit()
        return user.id


@dataclass
class Signed:
    """A signed-in browser: cookies already set on `client`, plus the CSRF header to send."""

    client: TestClient
    user_id: int
    csrf: str

    @property
    def headers(self) -> dict[str, str]:
        return {"X-CSRF-Token": self.csrf}


def sign_in_directly(url: str, client: TestClient, user_id: int) -> Signed:
    """Start a session without the login form (needed for consultants, who cannot use it yet)."""
    with db_session(url) as db:
        user = db.get(User, user_id)
        assert user is not None
        token, row = create_session(db, user, "127.0.0.1")
        db.commit()
        csrf = row.csrf_token
    client.cookies.set(cookie_name(client.app.state.settings), token)  # type: ignore[attr-defined]
    return Signed(client, user_id, csrf)


def login(client: TestClient, email: str, password: str = PASSWORD) -> Any:
    return client.post("/api/auth/login", json={"email": email, "password": password})


def sign_in(client: TestClient, email: str, password: str = PASSWORD) -> Signed:
    resp = login(client, email, password)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return Signed(client, body["user"]["id"], body["csrf_token"])
