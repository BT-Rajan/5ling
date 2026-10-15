"""Chunk C07: creating clients (BRD FR-CL-01 to FR-CL-03, FR-AL-08, CR-01)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.identifiers import gstin_check_char, gstin_problem, pan_problem
from app.models import Role
from app.routers.clients import normalise_phone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.audit_helpers import entries
from tests.helpers import Signed, break_glass, make_user, sign_in, sign_in_directly

PAN = "ABCPE1234F"  # an individual's PAN (4th letter P)
FIRM_GSTIN = "27AAPFU0939F1ZV"  # a published example; its PAN is AAPFU0939F (a firm)
COMPANY_GSTIN = "29AAGCB7383J1Z4"

# --- Validators (no database) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("pan", "ok"),
    [
        ("ABCPE1234F", True),
        ("AAPFU0939F", True),
        ("ABCDE1234F", False),
        ("ABCP1234F", False),
        ("1BCPE1234F", False),
    ],
)
def test_pan_format(pan: str, ok: bool) -> None:
    assert (pan_problem(pan) is None) is ok


def test_gstin_checksum_and_state() -> None:
    assert gstin_problem(FIRM_GSTIN) is None
    assert gstin_problem(COMPANY_GSTIN) is None
    assert gstin_check_char(FIRM_GSTIN[:14]) == "V"
    assert "last character" in (gstin_problem("27AAPFU0939F1ZW") or "")  # one character off
    assert "state code" in (
        gstin_problem("40AAPFU0939F1Z" + gstin_check_char("40AAPFU0939F1Z")) or ""
    )
    assert gstin_problem("27AAPFU0939F1Z") is not None  # too short


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("98765 43210", "+919876543210"),
        ("098765-43210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("+44 7700 900123", "+447700900123"),
    ],
)
def test_phone_numbers_are_stored_in_one_form(typed: str, stored: str) -> None:
    assert normalise_phone(typed) == stored


@pytest.mark.parametrize("typed", ["12345", "5876543210", "+0 1234 5678"])
def test_impossible_phone_numbers_are_refused(typed: str) -> None:
    with pytest.raises(ValueError, match="mobile number"):
        normalise_phone(typed)


# --- Through the API -------------------------------------------------------------------------


def _ok(resp: Any, status: int = 200) -> Any:
    assert resp.status_code == status, resp.text
    return resp.json()


@pytest.fixture
def admin(web: TestClient, migrated_db: str) -> Signed:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    return sign_in(web, "boss@firm.in")


def publish_package(web: TestClient, admin: Signed, name: str = "GST Monthly") -> int:
    filing = _ok(
        web.post(
            "/api/admin/filings",
            json={"code": f"F-{name[:20]}", "name": "Filing"},
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
    pkg = _ok(web.post("/api/admin/packages", json={"name": name}, headers=admin.headers), 201)
    draft = {
        "fee": "1000",
        "billing_frequency": "monthly",
        "filings": [{"filing_id": filing["id"]}],
    }
    _ok(web.put(f"/api/admin/packages/{pkg['id']}/draft", json=draft, headers=admin.headers))
    _ok(web.post(f"/api/admin/packages/{pkg['id']}/publish", headers=admin.headers))
    return int(pkg["id"])


def client_body(package: int, **over: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": "individual",
        "legal_name": "Asha  Rao",
        "pan": PAN,
        "package_id": package,
        "contacts": [
            {
                "name": "Asha Rao",
                "email": "Asha@Example.in",
                "phone": "98765 43210",
                "is_primary": True,
            }
        ],
    }
    body.update(over)
    return body


def create(web: TestClient, signed: Signed, body: dict[str, Any]) -> Any:
    return web.post("/api/clients", json=body, headers=signed.headers)


# --- Done when: duplicate PAN or GSTIN is blocked and links to the existing client; every
# client has a 6-digit ID --------------------------------------------------------------------


@pytest.mark.requires_db
def test_every_client_gets_a_six_digit_id_and_duplicates_point_to_it(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    first = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)
    assert first["client_id"] == 100001
    assert (first["legal_name"], first["pan_masked"], first["status"]) == (
        "Asha Rao",
        "AB•••••34F",
        "active",
    )
    assert "pan" not in first  # NFR-SE-06: masked unless asked for
    shown = _ok(web.post("/api/clients/100001/pan", headers=admin.headers))
    assert shown == {"pan": PAN}
    assert "client.pan_revealed" in [e.event for e in entries(migrated_db)]

    again = create(
        web,
        admin,
        client_body(pid, owner_id=admin.user_id, legal_name="Someone Else", pan="abcpe 1234 f"),
    )
    assert again.status_code == 409
    error = again.json()["error"]
    assert error["code"] == "duplicate_client"
    assert error["existing"] == {
        "client_id": 100001,
        "matched": "pan",
        "can_open": True,
        "legal_name": "Asha Rao",
    }
    assert error["message"] == "Client 100001 (Asha Rao) already has this PAN."

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
    assert firm["client_id"] == 100002
    # The PAN is taken from the GSTIN, so it is checked too.
    assert _ok(web.post("/api/clients/100002/pan", headers=admin.headers))["pan"] == "AAPFU0939F"
    by_gstin = create(
        web,
        admin,
        client_body(
            pid, kind="business", legal_name="x", pan=None, gstin=FIRM_GSTIN, owner_id=admin.user_id
        ),
    )
    assert by_gstin.json()["error"]["existing"]["client_id"] == 100002
    by_its_pan = create(
        web,
        admin,
        client_body(pid, kind="business", legal_name="x", pan="AAPFU0939F", owner_id=admin.user_id),
    )
    assert by_its_pan.json()["error"]["existing"] == {
        "client_id": 100002,
        "matched": "pan",
        "can_open": True,
        "legal_name": "U Firm",
    }

    # IDs are never typed, never changed, never reused, and clients are never deleted.
    engine = create_engine(migrated_db)
    try:
        for sql, message in (
            ("UPDATE clients SET client_id = 123456 WHERE client_id = 100001", "cannot be changed"),
            ("DELETE FROM clients WHERE client_id = 100002", "never deleted"),
            ("DELETE FROM client_id_counter", "final"),
        ):
            with pytest.raises(Exception, match=message), engine.begin() as conn:
                conn.execute(text(sql))
    finally:
        engine.dispose()
    assert _ok(web.get("/api/clients/100001"))["client_id"] == 100001


@pytest.mark.requires_db
def test_a_staff_member_is_told_whose_it_is_without_seeing_it(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)
    staff_id = make_user(migrated_db, "asha@firm.in", Role.STAFF)
    staff = sign_in_directly(migrated_db, web, staff_id)
    dup = create(web, staff, client_body(pid, legal_name="Copy"))
    assert dup.status_code == 409
    error = dup.json()["error"]
    assert error["existing"] == {
        "client_id": 100001,
        "matched": "pan",
        "can_open": False,
        "legal_name": None,
    }
    assert "Asha Rao" not in error["message"]
    assert "ask the Administrator" in error["message"]
    # And the existing client stays closed to them.
    assert web.get("/api/clients/100001").status_code == 403


# --- Who may create and open -----------------------------------------------------------------


@pytest.mark.requires_db
def test_staff_create_clients_they_own(web: TestClient, admin: Signed, migrated_db: str) -> None:
    pid = publish_package(web, admin)
    staff_id = make_user(migrated_db, "asha@firm.in", Role.STAFF, name="Asha Staff")
    staff = sign_in_directly(migrated_db, web, staff_id)
    options = _ok(web.get("/api/clients/options"))
    assert options["owners"] == [{"id": staff_id, "full_name": "Asha Staff"}]
    assert [p["name"] for p in options["packages"]] == ["GST Monthly"]
    made = _ok(create(web, staff, client_body(pid)), 201)
    assert made["owner"]["id"] == staff_id
    refused = create(web, staff, client_body(pid, pan="ABCPF1234G", owner_id=admin.user_id))
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "forbidden_owner"


@pytest.mark.requires_db
def test_only_the_owner_and_the_administrator_can_open_a_client(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    owner_id = make_user(migrated_db, "owner@firm.in", Role.STAFF)
    other_id = make_user(migrated_db, "other@firm.in", Role.STAFF)
    made = _ok(create(web, admin, client_body(pid, owner_id=owner_id)), 201)
    url = f"/api/clients/{made['client_id']}"
    # The Administrator, after giving a reason (FR-AC-05).
    assert web.get(url).json()["error"]["code"] == "reason_required"
    break_glass(web, admin, made["client_id"])
    assert web.get(url).status_code == 200
    sign_in_directly(migrated_db, web, other_id)
    assert web.get(url).status_code == 403
    sign_in_directly(migrated_db, web, owner_id)
    assert web.get(url).status_code == 200
    assert web.get("/api/clients/99999").status_code == 422  # not a 6-digit ID


@pytest.mark.requires_db
def test_owner_and_package_must_be_real_and_usable(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    consultant = make_user(migrated_db, "c@ca.in", Role.CONSULTANT)
    assert (
        create(web, admin, client_body(pid, owner_id=consultant)).json()["error"]["code"]
        == "bad_owner"
    )
    assert create(web, admin, client_body(pid)).json()["error"]["code"] == "owner_required"
    unpublished = _ok(
        web.post("/api/admin/packages", json={"name": "Not yet"}, headers=admin.headers), 201
    )["id"]
    assert (
        create(web, admin, client_body(unpublished, owner_id=admin.user_id)).json()["error"]["code"]
        == "bad_package"
    )
    _ok(web.patch(f"/api/admin/packages/{pid}", json={"is_active": False}, headers=admin.headers))
    assert (
        create(web, admin, client_body(pid, owner_id=admin.user_id)).json()["error"]["code"]
        == "bad_package"
    )


# --- What a client must have ---------------------------------------------------------------


@pytest.mark.requires_db
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"legal_name": " "}, "legal_name"),
        ({"package_id": None}, "package_id"),
        ({"contacts": []}, "contacts"),
        ({"pan": "ABCDE1234F"}, "not a valid PAN"),
        ({"pan": "AAPFU0939F"}, "belongs to a firm or LLP, not an individual"),
        ({"kind": "business", "gstin": "27AAPFU0939F1ZW"}, "last character"),
        ({"kind": "business", "gstin": FIRM_GSTIN}, "PAN inside the GSTIN is different"),
        ({"contacts": [{"name": "A", "email": "a@x.in"}]}, "exactly one contact as the primary"),
        (
            {
                "contacts": [
                    {"name": "A", "email": "a@x.in", "is_primary": True},
                    {"name": "B", "email": "b@x.in", "is_primary": True},
                ]
            },
            "exactly one",
        ),
        ({"contacts": [{"name": "A", "email": "not-an-email", "is_primary": True}]}, "email"),
        (
            {"contacts": [{"name": "A", "email": "a@x.in", "is_primary": True, "whatsapp": True}]},
            "needs a mobile number",
        ),
    ],
)
def test_incomplete_or_wrong_clients_are_refused(
    web: TestClient, admin: Signed, change: dict[str, Any], message: str
) -> None:
    pid = publish_package(web, admin)
    resp = create(web, admin, client_body(pid, owner_id=admin.user_id, **change))
    assert resp.status_code == 422, resp.text
    assert message in resp.text


@pytest.mark.requires_db
def test_a_client_keeps_the_package_version_it_was_given(web: TestClient, admin: Signed) -> None:
    pid = publish_package(web, admin)
    made = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)
    assert made["package"]["version"] == 1
    current = _ok(web.get(f"/api/admin/packages/{pid}"))["versions"][0]
    draft = {
        "fee": "2000",
        "billing_frequency": "monthly",
        "filings": [{"filing_id": f["filing_id"]} for f in current["filings"]],
    }
    _ok(web.put(f"/api/admin/packages/{pid}/draft", json=draft, headers=admin.headers))
    _ok(web.post(f"/api/admin/packages/{pid}/publish", headers=admin.headers))
    assert _ok(web.get(f"/api/clients/{made['client_id']}"))["package"]["version"] == 1
    newer = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id, pan="ABCPF1234G")), 201)
    assert newer["package"]["version"] == 2


# --- Contacts and consent --------------------------------------------------------------------


@pytest.mark.requires_db
def test_contacts_start_with_email_only_and_consent_changes_are_logged(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    contacts = [
        {"name": "Asha", "email": "ASHA@x.in", "phone": "98765 43210", "is_primary": True},
        {"name": "Ravi", "email": "ravi@x.in", "phone": "+91 91234 56789", "sms": True},
    ]
    made = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id, contacts=contacts)), 201)
    asha, ravi = made["contacts"]
    assert (asha["email"], asha["phone"], asha["is_primary"]) == (
        "asha@x.in",
        "+919876543210",
        True,
    )
    assert asha["consent"] == {"email": True, "whatsapp": False, "sms": False}
    assert ravi["consent"] == {"email": True, "whatsapp": False, "sms": True}

    url = f"/api/clients/{made['client_id']}/contacts/{asha['id']}/consent"
    turned_on = _ok(
        web.put(
            url,
            json={"channel": "whatsapp", "granted": True, "note": "Asked by phone"},
            headers=admin.headers,
        )
    )
    assert turned_on["consent"]["whatsapp"] is True
    _ok(
        web.put(url, json={"channel": "whatsapp", "granted": True}, headers=admin.headers)
    )  # no change, no row
    _ok(web.put(url, json={"channel": "whatsapp", "granted": False}, headers=admin.headers))
    email_off = web.put(url, json={"channel": "email", "granted": False}, headers=admin.headers)
    assert email_off.json()["error"]["code"] == "email_required"
    consent_events = [e for e in entries(migrated_db) if e.event == "client.consent_changed"]
    assert [e.details["granted"] for e in consent_events] == [True, False]

    engine = create_engine(migrated_db)
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT channel, granted FROM contact_consents ORDER BY id")
            ).all()
        assert [tuple(r) for r in rows][-2:] == [("whatsapp", 1), ("whatsapp", 0)]
        with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
            conn.execute(text("UPDATE contact_consents SET granted = 1"))
    finally:
        engine.dispose()
    other = web.put(
        f"/api/clients/{made['client_id']}/contacts/999999/consent",
        json={"channel": "sms", "granted": True},
        headers=admin.headers,
    )
    assert other.status_code == 404


@pytest.mark.requires_db
def test_identifiers_never_reach_the_audit_log(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)
    (created,) = [e for e in entries(migrated_db) if e.event == "client.created"]
    assert (created.target_type, created.target_id) == ("client", "100001")
    recorded = json.dumps(created.details)
    for secret in (PAN, "asha@example.in", "9876543210"):
        assert secret not in recorded
