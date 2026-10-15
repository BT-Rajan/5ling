"""Test helpers: create people and sessions directly, so each test states only what it needs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.auth import mfa
from app.auth.passwords import hash_password
from app.auth.sessions import cookie_name, create_session
from app.db import make_engine, make_session_factory
from app.models import Role, User
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from tests.conftest import GOOD_SECRET

TEST_TOTP_SECRET = "JBSWY3DPEHPK3PXP"  # the authenticator secret every test user shares
PASSWORD = "correct horse battery"
_HASH = hash_password(PASSWORD)  # hashed once; Argon2 is deliberately slow


def db_session(url: str) -> Session:
    return make_session_factory(make_engine(url))()


def give_filing_proof(db: Session, cycle_id: int) -> None:
    """Record filing proof for a cycle, so a test may set its stage to Filed directly (C23: the
    database refuses Filed without proof)."""
    from app.models import Cycle, CycleFiling, Document, DocumentVersion, ScanStatus, UploadedVia
    from sqlalchemy import select

    cycle = db.get(Cycle, cycle_id)
    user_id = db.scalar(select(User.id).order_by(User.id).limit(1))
    if cycle is None or user_id is None:
        raise RuntimeError("give_filing_proof needs the cycle and at least one user")
    doc = Document(
        client_pk=cycle.client_pk,
        cycle_id=cycle.id,
        title="Filing acknowledgement",
        latest_version=1,
    )
    db.add(doc)
    db.flush()
    version = DocumentVersion(
        document_id=doc.id,
        version=1,
        storage_key=f"test-proof-{cycle.id}",
        filename="ack.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256="0" * 64,
        uploaded_via=UploadedVia.STAFF,
        scan_status=ScanStatus.CLEAN,
    )
    db.add(version)
    db.flush()
    db.add(
        CycleFiling(
            cycle_id=cycle.id,
            reference="TEST12345",
            acknowledgement_version_id=version.id,
            filed_by_id=user_id,
        )
    )
    db.flush()


def make_user(
    url: str,
    email: str,
    role: Role = Role.STAFF,
    *,
    password: bool = True,
    active: bool = True,
    must_change: bool = False,
    name: str = "Test Person",
    two_step: bool | None = None,
    **extra: Any,
) -> int:
    """Create a person. Staff and Administrators are enrolled in two-step sign-in unless told not."""
    if role is Role.CONSULTANT:
        extra.setdefault("agreement_signed_on", date(2026, 1, 15))
        password = False
    enrolled = (role is not Role.CONSULTANT) if two_step is None else two_step
    if enrolled:
        extra["totp_secret_enc"] = mfa.encrypt_secret(TEST_TOTP_SECRET, GOOD_SECRET)
        extra["totp_enabled_at"] = utcnow()
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
    """First step only: the password."""
    return client.post("/api/auth/login", json={"email": email, "password": password})


def current_code(at: datetime | None = None) -> str:
    return mfa.totp_at(TEST_TOTP_SECRET, at or utcnow())


def forget_last_code(client: TestClient, email: str) -> None:
    """Replay protection refuses a code used twice in 30 seconds; tests sign in more often than that."""
    url = client.app.state.settings.database_url.get_secret_value()  # type: ignore[attr-defined]
    with db_session(url) as db:
        db.execute(update(User).where(User.email == email).values(totp_last_step=None))
        db.commit()


def sign_in(
    client: TestClient, email: str, password: str = PASSWORD, *, at: datetime | None = None
) -> Signed:
    """Both steps: password, then the current code."""
    resp = login(client, email, password)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    if body["status"] == "mfa_required":
        forget_last_code(client, email)
        resp = client.post(
            "/api/auth/mfa/verify", json={"challenge": body["challenge"], "code": current_code(at)}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
    assert body["status"] == "signed_in", body
    return Signed(client, body["user"]["id"], body["csrf_token"])
