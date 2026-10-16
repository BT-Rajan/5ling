"""Chunk C08: changing, searching and reassigning clients (FR-CL-04, FR-CL-06, FR-AC-03, CR-01)."""

from __future__ import annotations

from typing import Any

import pytest
from app.models import Role
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.audit_helpers import events
from tests.helpers import Signed, break_glass, make_user, sign_in_directly
from tests.test_clients import FIRM_GSTIN, _ok, admin, client_body, create, publish_package

__all__ = ["admin"]  # the fixture, re-used from test_clients

pytestmark = pytest.mark.requires_db


def _client(web: TestClient, signed: Signed, package: int, **over: Any) -> int:
    return int(_ok(create(web, signed, client_body(package, **over)), 201)["client_id"])


# --- Done when: the previous owner sees 'no access' on the next request after reassignment ---


def test_reassigning_moves_access_on_the_very_next_request(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    first_id = make_user(migrated_db, "first@firm.in", Role.STAFF, name="First Owner")
    second_id = make_user(migrated_db, "second@firm.in", Role.STAFF, name="Second Owner")
    cid = _client(web, admin, pid, owner_id=first_id)
    url = f"/api/clients/{cid}"

    first = TestClient(web.app)
    sign_in_directly(migrated_db, first, first_id)
    second = TestClient(web.app)
    sign_in_directly(migrated_db, second, second_id)
    assert first.get(url).status_code == 200
    assert second.get(url).status_code == 403

    break_glass(web, admin, cid)  # not the Administrator's own client (C29)
    moved = web.patch(
        url,
        json={"owner_id": second_id, "reason": "First Owner is on leave"},
        headers=admin.headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["owner"] == {"id": second_id, "full_name": "Second Owner"}

    assert first.get(url).status_code == 403  # the same session, the next request
    assert first.get(f"{url}/history").status_code == 403
    assert first.get("/api/clients").json()["total"] == 0
    assert second.get(url).status_code == 200
    assert [c["client_id"] for c in second.get("/api/clients").json()["items"]] == [cid]

    history = _ok(web.get(f"{url}/history"))
    assert [(h["field"], h["old"], h["new"], h["reason"]) for h in history] == [
        ("owner", "First Owner", "Second Owner", "First Owner is on leave")
    ]
    assert history[0]["changed_by"]["id"] == admin.user_id
    assert "client.owner_changed" in events(migrated_db)


# --- Search -------------------------------------------------------------------------------


def test_search_by_name_pan_gstin_and_client_id(web: TestClient, admin: Signed) -> None:
    pid = publish_package(web, admin)
    asha = _client(web, admin, pid, owner_id=admin.user_id, legal_name="Asha Rao")
    firm = _client(
        web,
        admin,
        pid,
        owner_id=admin.user_id,
        kind="business",
        legal_name="Rao & Sons",
        pan=None,
        gstin=FIRM_GSTIN,
    )
    _client(web, admin, pid, owner_id=admin.user_id, legal_name="Zeta 100% Ltd", pan="ABCPF1234G")

    def found(**params: Any) -> list[int]:
        resp = web.get("/api/clients", params=params)
        assert resp.status_code == 200, resp.text
        return [c["client_id"] for c in resp.json()["items"]]

    assert found(q="rao") == [asha, firm]  # by name, sorted by name
    assert found(q="abcpe 1234 f") == [asha]  # by PAN, however it was typed
    assert found(q=FIRM_GSTIN.lower()) == [firm]
    assert found(q=str(firm)) == [firm]  # by client ID
    assert found(q="100%") == [found(q="Zeta")[0]]  # % is a character here, not "anything"
    assert found(q="_") == []
    row = web.get("/api/clients", params={"q": "Asha"}).json()["items"][0]
    assert row["pan_masked"] == "AB•••••34F"
    assert "ABCPE1234F" not in str(row)


def test_filters_and_paging(web: TestClient, admin: Signed, migrated_db: str) -> None:
    gst = publish_package(web, admin, "GST")
    itr = publish_package(web, admin, "ITR")
    staff_id = make_user(migrated_db, "s@firm.in", Role.STAFF)
    pans = iter(["ABCPA1111A", "ABCPB2222B", "ABCPC3333C", "ABCPD4444D"])
    a = _client(web, admin, gst, owner_id=staff_id, pan=next(pans), legal_name="A")
    b = _client(web, admin, itr, owner_id=admin.user_id, pan=next(pans), legal_name="B")
    c = _client(web, admin, gst, owner_id=admin.user_id, pan=next(pans), legal_name="C")
    _ok(
        web.patch(
            f"/api/clients/{c}",
            json={"status": "paused", "reason": "Client asked"},
            headers=admin.headers,
        )
    )

    def ids(**params: Any) -> list[int]:
        return [x["client_id"] for x in web.get("/api/clients", params=params).json()["items"]]

    assert ids(owner_id=staff_id) == [a]
    assert ids(package_id=itr) == [b]
    assert ids(status="paused") == [c]
    page = web.get("/api/clients", params={"limit": 2, "offset": 2}).json()
    assert (page["total"], [x["client_id"] for x in page["items"]]) == (3, [c])
    assert web.get("/api/clients", params={"limit": 101}).status_code == 422


def test_staff_search_only_their_own_clients(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    mine_id = make_user(migrated_db, "mine@firm.in", Role.STAFF)
    _client(web, admin, pid, owner_id=admin.user_id, legal_name="Not yours")
    mine = _client(web, admin, pid, owner_id=mine_id, legal_name="Yours", pan="ABCPF1234G")
    sign_in_directly(migrated_db, web, mine_id)
    assert [c["client_id"] for c in web.get("/api/clients").json()["items"]] == [mine]
    # Asking for someone else's clients, or someone else's client by ID or PAN, finds nothing.
    assert web.get("/api/clients", params={"owner_id": admin.user_id}).json()["total"] == 0
    assert web.get("/api/clients", params={"q": "100001"}).json()["total"] == 0
    assert web.get("/api/clients", params={"q": "ABCPE1234F"}).json()["total"] == 0


# --- Changes ------------------------------------------------------------------------------


def test_every_change_needs_a_reason_and_is_kept(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    cid = _client(web, admin, pid, owner_id=admin.user_id)
    url = f"/api/clients/{cid}"
    assert web.patch(url, json={"status": "paused"}, headers=admin.headers).status_code == 422
    assert (
        web.patch(url, json={"status": "paused", "reason": "x"}, headers=admin.headers).status_code
        == 422
    )
    assert (
        web.patch(url, json={"reason": "No change at all"}, headers=admin.headers).status_code
        == 422
    )
    same = web.patch(
        url, json={"status": "active", "reason": "Already active"}, headers=admin.headers
    )
    assert same.json()["error"]["code"] == "no_change"

    _ok(
        web.patch(
            url, json={"status": "closed", "reason": "Engagement ended"}, headers=admin.headers
        )
    )
    history = _ok(web.get(f"{url}/history"))
    assert [(h["field"], h["old"], h["new"]) for h in history] == [("status", "active", "closed")]

    engine = create_engine(migrated_db)
    try:
        with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
            conn.execute(text("UPDATE client_changes SET reason = 'rewritten'"))
    finally:
        engine.dispose()


def test_a_new_package_version_is_a_recorded_change(web: TestClient, admin: Signed) -> None:
    gst = publish_package(web, admin, "GST")
    itr = publish_package(web, admin, "ITR")
    cid = _client(web, admin, gst, owner_id=admin.user_id)
    moved = _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"package_id": itr, "reason": "Moved to ITR"},
            headers=admin.headers,
        )
    )
    assert moved["package"]["name"] == "ITR"
    (change,) = _ok(web.get(f"/api/clients/{cid}/history"))
    assert (change["field"], change["old"], change["new"]) == (
        "package",
        "GST (version 1)",
        "ITR (version 1)",
    )
    unpublished = _ok(
        web.post("/api/admin/packages", json={"name": "Draft only"}, headers=admin.headers), 201
    )["id"]
    bad = web.patch(
        f"/api/clients/{cid}",
        json={"package_id": unpublished, "reason": "Try it"},
        headers=admin.headers,
    )
    assert bad.json()["error"]["code"] == "bad_package"


def test_only_the_administrator_reopens_a_closed_client(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    staff_id = make_user(migrated_db, "s@firm.in", Role.STAFF)
    cid = _client(web, admin, pid, owner_id=staff_id)
    staff = TestClient(web.app)
    signed = sign_in_directly(migrated_db, staff, staff_id)
    url = f"/api/clients/{cid}"
    _ok(
        staff.patch(
            url, json={"status": "closed", "reason": "Engagement ended"}, headers=signed.headers
        )
    )
    reopen = staff.patch(
        url, json={"status": "active", "reason": "Back again"}, headers=signed.headers
    )
    assert reopen.json()["error"]["code"] == "reopen_forbidden"
    break_glass(web, admin, cid)
    _ok(web.patch(url, json={"status": "active", "reason": "Back again"}, headers=admin.headers))


def test_staff_cannot_change_someone_elses_client_or_hand_to_a_consultant(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    staff_id = make_user(migrated_db, "s@firm.in", Role.STAFF)
    consultant = make_user(migrated_db, "c@ca.in", Role.CONSULTANT)
    theirs = _client(web, admin, pid, owner_id=admin.user_id)
    bad_owner = web.patch(
        f"/api/clients/{theirs}",
        json={"owner_id": consultant, "reason": "Try it"},
        headers=admin.headers,
    )
    assert bad_owner.json()["error"]["code"] == "bad_owner"
    staff = TestClient(web.app)
    signed = sign_in_directly(migrated_db, staff, staff_id)
    refused = staff.patch(
        f"/api/clients/{theirs}",
        json={"status": "paused", "reason": "Not mine"},
        headers=signed.headers,
    )
    assert refused.status_code == 403
    assert _ok(web.get(f"/api/clients/{theirs}"))["status"] == "active"
