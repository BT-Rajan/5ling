"""Chunk C17: each cycle's checklist, and client uploads in the portal (BRD FR-AL-04, FR-AL-05)."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import pytest
from app.models import CycleChecklistItem, DocumentVersion, UploadedVia
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session
from tests.test_clients import _ok, admin, client_body, create
from tests.test_documents import EICAR, PDF, FakeScanner, scanner
from tests.test_tickets import first_ticket

__all__ = ["admin", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db

CHECKLIST = [
    {"label": "Sales register", "required": True},
    {"label": "Purchase register", "required": True},
    {"label": "Notes for us", "required": False},
]


def publish_with_checklist(
    web: TestClient, admin: Signed, checklist: list[dict[str, Any]]
) -> tuple[int, int]:
    filing = _ok(
        web.post(
            "/api/admin/filings",
            json={"code": "GSTR-1", "name": "Outward supplies"},
            headers=admin.headers,
        ),
        201,
    )
    cal = f"/api/admin/filings/{filing['id']}/calendar"
    _ok(
        web.put(
            f"{cal}/draft",
            json={"frequency": "monthly", "due": {"months_after": 1, "day": 11}},
            headers=admin.headers,
        )
    )
    _ok(web.post(f"{cal}/publish", headers=admin.headers))
    pkg = _ok(web.post("/api/admin/packages", json={"name": "GST"}, headers=admin.headers), 201)
    draft = {
        "fee": "1000",
        "billing_frequency": "monthly",
        "filings": [{"filing_id": filing["id"], "checklist": checklist}],
    }
    _ok(web.put(f"/api/admin/packages/{pkg['id']}/draft", json=draft, headers=admin.headers))
    _ok(web.post(f"/api/admin/packages/{pkg['id']}/publish", headers=admin.headers))
    return int(pkg["id"]), int(filing["id"])


def open_portal(web: TestClient, admin: Signed, code: str) -> tuple[TestClient, str]:
    url = _ok(
        web.post(f"/api/tickets/{code}/links", json={"send_email": False}, headers=admin.headers),
        201,
    )["url"]
    client = TestClient(web.app)
    csrf = _ok(client.post("/api/portal/open", json={"token": url.split("#t=", 1)[1]}))[
        "csrf_token"
    ]
    return client, csrf


def portal_upload(client: TestClient, csrf: str, item_id: int, name: str, data: bytes) -> Any:
    return client.post(
        f"/api/uploads/portal/items/{item_id}",
        content=data,
        headers={
            "X-File-Name": quote(name),
            "X-CSRF-Token": csrf,
            "Content-Type": "application/octet-stream",
        },
    )


def setup(web: TestClient, admin: Signed, url: str) -> tuple[int, str]:
    pid, _ = publish_with_checklist(web, admin, CHECKLIST)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    return cid, first_ticket(url, cid)


# --- LL-073: every cycle gets its own copy of the package's checklist --------------------------


def test_each_cycle_copies_the_checklist_of_its_package_version(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup(web, admin, migrated_db)
    items = _ok(web.get(f"/api/tickets/{code}/checklist"))
    assert [(i["label"], i["required"], i["status"]) for i in items] == [
        ("Sales register", True, "pending"),
        ("Purchase register", True, "pending"),
        ("Notes for us", False, "pending"),
    ]
    with db_session(migrated_db) as db:
        per_cycle = db.execute(
            text("SELECT COUNT(DISTINCT cycle_id), COUNT(*) FROM cycle_checklist_items")
        ).one()
        cycles = db.scalar(text("SELECT COUNT(*) FROM cycles"))
    assert per_cycle == (cycles, cycles * 3)
    engine = create_engine(migrated_db)
    try:
        for sql in (
            "UPDATE cycle_checklist_items SET label = 'Changed'",
            "DELETE FROM cycle_checklist_items",
        ):
            with (
                pytest.raises(Exception, match=r"cannot be changed|are kept"),
                engine.begin() as conn,
            ):
                conn.execute(text(sql))
    finally:
        engine.dispose()


# --- Done when: rejected files show a reason to the client and appear in the log ----------------


def test_the_client_uploads_per_item_and_sees_what_is_still_needed(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    _, code = setup(web, admin, migrated_db)
    client, csrf = open_portal(web, admin, code)
    checklist = _ok(client.get("/api/portal/checklist"))
    assert checklist["still_needed"] == 2
    assert "PDF" in checklist["allowed"]
    sales = checklist["items"][0]["id"]

    after = _ok(portal_upload(client, csrf, sales, "April sales.pdf", PDF), 201)
    assert after["still_needed"] == 1
    first = after["items"][0]
    assert (first["status"], first["status_label"]) == ("received", "Received")
    assert [(f["version"], f["filename"], f["checked"]) for f in first["files"]] == [
        (1, "April sales.pdf", True)
    ]
    # A second upload for the same item is version 2 of the same document.
    again = _ok(portal_upload(client, csrf, sales, "April sales (fixed).pdf", PDF + b"%"), 201)
    assert [f["version"] for f in again["items"][0]["files"]] == [2, 1]

    # Staff see it against the item, as versions uploaded by the client.
    item = _ok(web.get(f"/api/tickets/{code}/checklist"))[0]
    assert item["status"] == "received"
    docs = _ok(web.get(f"/api/tickets/{code}/documents"))
    assert [(d["id"], d["title"]) for d in docs] == [(item["document_id"], "Sales register")]
    assert [(v["version"], v["uploaded_via"], v["uploaded_by"]) for v in docs[0]["versions"]] == [
        (2, "portal", None),
        (1, "portal", None),
    ]
    with db_session(migrated_db) as db:
        assert {v.uploaded_via for v in db.scalars(select(DocumentVersion))} == {UploadedVia.PORTAL}
    uploaded = [e for e in entries(migrated_db) if e.event == "document.uploaded"]
    assert [e.actor_role for e in uploaded] == ["client", "client"]


def test_a_refused_file_tells_the_client_why_and_is_logged(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    _, code = setup(web, admin, migrated_db)
    client, csrf = open_portal(web, admin, code)
    item = _ok(client.get("/api/portal/checklist"))["items"][1]["id"]

    wrong = portal_upload(client, csrf, item, "purchases.exe", b"MZ\x90\x00")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["message"].startswith("This kind of file is not accepted. Use ")
    fake = portal_upload(client, csrf, item, "purchases.pdf", b"MZ\x90\x00")
    assert fake.json()["error"]["message"] == "This file is not really a PDF file."
    virus = portal_upload(client, csrf, item, "purchases.txt", EICAR)
    assert virus.json()["error"]["code"] == "infected"
    assert "virus was found" in virus.json()["error"]["message"]

    logged = [
        e for e in entries(migrated_db) if e.event in {"document.rejected", "document.infected"}
    ]
    assert [(e.event, e.actor_role, e.details["item"]) for e in logged] == [
        ("document.rejected", "client", item),
        ("document.rejected", "client", item),
        ("document.infected", "client", item),
    ]
    assert logged[1].details["reason"] == "This file is not really a PDF file."
    assert logged[2].details["reason"] == "Eicar-Test-Signature"
    assert _ok(client.get("/api/portal/checklist"))["items"][1]["status"] == "pending"


def test_the_portal_reaches_only_its_own_cycle(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    _, code = setup(web, admin, migrated_db)
    client, csrf = open_portal(web, admin, code)
    mine = {i["id"] for i in _ok(client.get("/api/portal/checklist"))["items"]}
    with db_session(migrated_db) as db:
        other = db.scalar(
            select(CycleChecklistItem.id).where(CycleChecklistItem.id.not_in(mine)).limit(1)
        )
    assert other is not None
    assert portal_upload(client, csrf, other, "a.pdf", PDF).status_code == 404
    item = next(iter(mine))
    assert portal_upload(client, "wrong", item, "a.pdf", PDF).status_code == 403
    stranger = TestClient(web.app)
    assert stranger.get("/api/portal/checklist").status_code == 401
    assert portal_upload(stranger, csrf, item, "a.pdf", PDF).status_code == 401
    with db_session(migrated_db) as db:
        assert db.scalar(select(DocumentVersion.id)) is None


def test_staff_can_upload_against_an_item_too(
    web: TestClient, admin: Signed, migrated_db: str, scanner: FakeScanner
) -> None:
    _, code = setup(web, admin, migrated_db)
    item = _ok(web.get(f"/api/tickets/{code}/checklist"))[2]
    sent = web.post(
        f"/api/uploads/tickets/{code}",
        params={"item_id": item["id"]},
        content=PDF,
        headers={**admin.headers, "X-File-Name": "notes.pdf"},
    )
    assert sent.status_code == 201, sent.text
    assert _ok(web.get(f"/api/tickets/{code}/checklist"))[2]["status"] == "received"
    bad = web.post(
        f"/api/uploads/tickets/{code}",
        params={"item_id": 999999},
        content=PDF,
        headers={**admin.headers, "X-File-Name": "notes.pdf"},
    )
    assert bad.json()["error"]["message"] == "That checklist item is not on this filing."
