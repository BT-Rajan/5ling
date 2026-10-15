"""Chunk C30: security hardening checks (BRD NFR-SE-04, NFR-SE-05, NFR-SE-06, FR-AU-01).

- Log review: a scripted run through the screens that handle PANs and GSTINs writes none of them
  to the logs or to the audit log (NFR-SE-06).
- No file is reachable without signing in, and none is stored as plain text (NFR-SE-04/05).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from app.logging_conf import RedactingJsonFormatter
from app.models import Role
from fastapi.testclient import TestClient

from tests.audit_helpers import entries
from tests.conftest import TEST_STORAGE
from tests.helpers import Signed, make_user, sign_in_directly
from tests.routewalk import PUBLIC, protected_routes
from tests.test_clients import (
    COMPANY_GSTIN,
    FIRM_GSTIN,
    PAN,
    _ok,
    admin,
    client_body,
    create,
    publish_package,
)
from tests.test_documents import PDF, scanner, setup_ticket

__all__ = ["admin", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db

SECRETS = (PAN, FIRM_GSTIN, FIRM_GSTIN[2:12], COMPANY_GSTIN, COMPANY_GSTIN[2:12])


def written(records: list[logging.LogRecord]) -> list[str]:
    """Each record exactly as the app writes it."""
    formatter = RedactingJsonFormatter()
    return [formatter.format(r) for r in records]


# --- LL-124: no PAN or GSTIN in the logs or the audit log; PAN masked unless asked ------------


def test_a_scripted_run_writes_no_pan_or_gstin_to_any_log(
    web: TestClient, admin: Signed, migrated_db: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    pid = publish_package(web, admin)
    person = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)
    firm = _ok(
        create(
            web,
            admin,
            client_body(
                pid,
                kind="business",
                legal_name="U Firm",
                pan=None,
                gstin=FIRM_GSTIN,
                owner_id=admin.user_id,
            ),
        ),
        201,
    )
    company = _ok(
        create(
            web,
            admin,
            client_body(
                pid,
                kind="business",
                legal_name="B Ltd",
                pan=None,
                gstin=COMPANY_GSTIN,
                owner_id=admin.user_id,
            ),
        ),
        201,
    )
    # Searching by the identifiers, duplicates, a bad one, reading and revealing.
    for q in (PAN, FIRM_GSTIN, COMPANY_GSTIN, PAN.lower()):
        _ok(web.get("/api/clients", params={"q": q}))
    assert create(web, admin, client_body(pid, owner_id=admin.user_id)).status_code == 409
    assert create(web, admin, client_body(pid, pan=PAN[:-1] + "1")).status_code == 422
    for cid in (person["client_id"], firm["client_id"], company["client_id"]):
        _ok(web.get(f"/api/clients/{cid}"))
        _ok(web.post(f"/api/clients/{cid}/pan", headers=admin.headers))
    # A staff member who may not see the client is refused (and that is logged).
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF)
    sita = TestClient(web.app)
    sign_in_directly(migrated_db, sita, sid)
    assert sita.get(f"/api/clients/{person['client_id']}").status_code == 403

    lines = written(caplog.records)
    assert lines  # the run did log
    for line in lines:
        for secret in SECRETS:
            assert secret not in line, line
    audit_text = [json.dumps(e.details) + str(e.target_id) for e in entries(migrated_db)]
    for text in audit_text:
        for secret in SECRETS:
            assert secret not in text, text

    # On screen the PAN is masked unless someone asks, and asking is recorded.
    assert "pan" not in _ok(web.get(f"/api/clients/{person['client_id']}"))
    reveals = [e for e in entries(migrated_db) if e.event == "client.pan_revealed"]
    assert len(reveals) == 3


# --- No unauthenticated file access; nothing stored as plain text -------------------------------


def test_no_file_route_answers_without_signing_in(
    web: TestClient, admin: Signed, migrated_db: str, scanner: object
) -> None:
    code = setup_ticket(web, admin, migrated_db)
    docs = _ok(
        web.post(
            f"/api/uploads/tickets/{code}",
            params={"title": "Sales register"},
            content=PDF + b"%secret sales",
            headers={**admin.headers, "X-File-Name": "sales.pdf"},
        ),
        201,
    )
    version = docs[0]["versions"][0]["id"]
    link = _ok(web.post(f"/api/documents/versions/{version}/link", headers=admin.headers))["url"]
    assert web.get(link).content.startswith(b"%PDF")  # the person it was made for

    stranger = TestClient(web.app)  # not signed in
    for method, path in protected_routes(web.app):  # type: ignore[arg-type]
        if any(word in path for word in ("files", "uploads", "documents")):
            url = (
                path.replace("{ticket}", code)
                .replace("{version_id}", str(version))
                .replace("{token}", link.rsplit("/", 1)[-1])
                .replace("{assignment_id}", "1")
                .replace("{item_id}", "1")
            )
            got = stranger.request(method, url)
            assert got.status_code == 401, (method, path, got.status_code)
    assert stranger.get(link).status_code == 401
    # Someone else signed in cannot use the link either.
    other = TestClient(web.app)
    sign_in_directly(
        migrated_db, other, make_user(migrated_db, "boss2@firm.in", Role.ADMINISTRATOR)
    )
    assert other.get(link).status_code in (403, 404, 410)
    # The only public upload is the client portal's, which needs its own portal session.
    assert {p for _, p in PUBLIC if "uploads" in p} == {"/api/uploads/portal/items/{item_id}"}
    portal = stranger.post(
        "/api/uploads/portal/items/1", content=PDF, headers={"X-File-Name": "a.pdf"}
    )
    assert portal.status_code == 401, portal.text

    # Stored files are encrypted: no plain text on disk.
    stored = [f for f in Path(TEST_STORAGE).rglob("*") if f.is_file()]
    assert stored
    for blob in stored:
        raw = blob.read_bytes()
        assert b"%PDF" not in raw, blob
        assert b"secret sales" not in raw, blob


# --- LL-125: encryption at rest and key separation, checked on the installation ----------------


def test_the_security_check_reports_keys_and_database_encryption(migrated_db: str) -> None:
    from app.db import make_engine
    from app.security_check import database_checks, key_checks

    from tests.conftest import make_settings

    good = {
        c.name: c.ok
        for c in key_checks(make_settings(env="production", allowed_hosts="app.example.test"))
    }
    assert good == {
        "production mode": True,
        "document key set": True,
        "document key kept apart": True,
    }
    dev = {c.name: c.ok for c in key_checks(make_settings())}
    assert dev["production mode"] is False
    assert dev["document key set"] is False

    engine = make_engine(migrated_db)
    try:
        checks = {c.name: c for c in database_checks(engine)}
    finally:
        engine.dispose()
    assert set(checks) == {
        "tables encrypted at rest",
        "new tables encrypted by default",
        "redo log encrypted",
        "undo log encrypted",
        "binary log encrypted",
        "TLS required for database connections",
    }
    # The test server has no keyring, so this reports what is missing rather than passing.
    assert "tables encrypted" in checks["tables encrypted at rest"].detail
    assert checks["tables encrypted at rest"].detail.split(" of ")[1].split(" ")[0].isdigit()
