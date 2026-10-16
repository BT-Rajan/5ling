"""Chunk C33: importing clients from a spreadsheet (FR-CL-05).

Rows with problems are listed with reasons; valid rows save only after confirmation.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.client_import import COLUMNS
from app.models import Role
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_clients import COMPANY_GSTIN, FIRM_GSTIN, PAN, _ok, admin, publish_package

__all__ = ["admin"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db

HEADER = ",".join(COLUMNS)
OTHER_PAN = "ABCPF1234G"


def check(browser: TestClient, signed: Signed, csv_text: str) -> Any:
    return browser.post(
        "/api/clients/import/check",
        content=csv_text.encode(),
        headers={**signed.headers, "Content-Type": "text/csv"},
    )


def save(browser: TestClient, signed: Signed, csv_text: str, token: str) -> Any:
    return browser.post(
        "/api/clients/import/save",
        params={"token": token},
        content=csv_text.encode(),
        headers={**signed.headers, "Content-Type": "text/csv"},
    )


def clients(url: str) -> int:
    with db_session(url) as db:
        return int(db.scalar(text("SELECT COUNT(*) FROM clients")) or 0)


def sheet(owner: str) -> str:
    return "\n".join(
        [
            HEADER,
            f"A1,individual,Asha Rao,{PAN},,{owner},GST Monthly,Asha Rao,asha@example.in,,,",
            f"A2,business,U Firm,,{FIRM_GSTIN},{owner},gst monthly,Accounts,acc@ufirm.in,,,",
            f"A3,individual,Bad Pan,{OTHER_PAN[:-1]}1,,{owner},GST Monthly,B,b@example.in,,,",
            f"A4,business,B Ltd,,{COMPANY_GSTIN},{owner},No Such Package,F,f@bltd.in,,,",
        ]
    )


def test_the_report_lists_problems_and_nothing_is_saved_until_confirmed(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    publish_package(web, admin)
    data = sheet("boss@firm.in")  # the admin fixture
    report = _ok(check(web, admin, data))
    assert (report["rows"], report["ready"]) == (4, 2)
    assert [(p["line"], p["source_id"]) for p in report["problems"]] == [(4, "A3"), (5, "A4")]
    assert report["problems"][0]["problems"][0].startswith("pan:")
    assert "No Such Package" in report["problems"][1]["problems"][0]
    assert OTHER_PAN[:-1] not in str(report)  # rows are named, identifiers are not
    assert clients(migrated_db) == 0  # checking saves nothing
    assert "client.import_checked" in [e.event for e in entries(migrated_db)]

    done = _ok(save(web, admin, data, report["token"]))
    assert [(s["line"], s["source_id"]) for s in done["saved"]] == [(2, "A1"), (3, "A2")]
    assert [p["source_id"] for p in done["skipped"]] == ["A3", "A4"]
    assert clients(migrated_db) == 2
    with db_session(migrated_db) as db:
        assert db.scalar(text("SELECT COUNT(*) FROM cycles")) > 0  # cycles made as usual
    logged = [e for e in entries(migrated_db) if e.event in ("client.created", "client.imported")]
    assert [e.event for e in logged] == ["client.created", "client.created", "client.imported"]
    assert logged[-1].details["saved"] == 2

    # Saving the same file again adds nothing twice.
    again = _ok(save(web, admin, data, _ok(check(web, admin, data))["token"]))
    assert again["saved"] == []
    assert any("already has this PAN or GSTIN" in p["problems"][-1] for p in again["skipped"])
    assert clients(migrated_db) == 2


def test_saving_needs_a_check_of_this_exact_file_by_this_person(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    publish_package(web, admin)
    data = sheet("boss@firm.in")  # the admin fixture
    token = _ok(check(web, admin, data))["token"]
    for body, tok in (
        (data + "\nA5,individual,Late Row,,,x,GST Monthly,L,l@x.in,,,", token),  # changed file
        (data, token[:-1] + ("0" if token[-1] != "0" else "1")),  # forged token
        (data, "9999999999.deadbeef"),
        (data, "1.0000000000"),  # expired
    ):
        refused = save(web, admin, body, tok)
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "check_first")
    # Another person's check does not count for this person.
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF)
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    assert save(sita, sita_s, data, token).status_code == 409
    assert clients(migrated_db) == 0


def test_staff_import_clients_they_own_and_only_those(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    publish_package(web, admin)
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF, name="Sita Staff")
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    data = "\n".join(
        [
            "kind,legal_name,pan,package,contact_name,contact_email,owner_email",
            f"individual,Asha Rao,{PAN},GST Monthly,Asha,asha@example.in,",
            f"individual,Ravi Rao,{OTHER_PAN},GST Monthly,Ravi,ravi@example.in,boss@firm.in",
        ]
    )
    report = _ok(check(sita, sita_s, data))
    assert report["ready"] == 1
    assert "Staff import clients they own" in report["problems"][0]["problems"][0]
    done = _ok(save(sita, sita_s, data, report["token"]))
    cid = done["saved"][0]["client_id"]
    assert _ok(sita.get(f"/api/clients/{cid}"))["owner"]["id"] == sid
    # Consultants may not import.
    cons = TestClient(web.app)
    signed = sign_in_directly(
        migrated_db, cons, make_user(migrated_db, "c@consult.in", Role.CONSULTANT)
    )
    assert check(cons, signed, data).status_code == 403


def test_a_file_that_cannot_be_read_is_refused_whole(web: TestClient, admin: Signed) -> None:
    for body, says in (
        ("legal_name,pan\nA,B", "The header row lacks"),
        (HEADER, "no rows under the header"),
        (HEADER + ",colour\n", "Unknown columns: colour"),
        (HEADER + "\n" + ",".join(["x"] * 13), "more cells than the header"),
    ):
        refused = check(web, admin, body)
        assert (refused.status_code, refused.json()["error"]["code"]) == (422, "bad_import_file")
        assert says in refused.json()["error"]["message"], body
    latin = web.post(
        "/api/clients/import/check",
        content=(HEADER + "\nA,individual,Jos\xe9").encode("latin-1"),
        headers={**admin.headers, "Content-Type": "text/csv"},
    )
    assert "CSV UTF-8" in latin.json()["error"]["message"]
