"""Chunk C05: packages and filing calendars, versioned (BRD FR-PK-01, FR-PK-02, FR-PK-05)."""

from __future__ import annotations

from typing import Any

import pytest
from app.models import Role
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from tests.audit_helpers import events
from tests.helpers import Signed, make_user, sign_in

pytestmark = pytest.mark.requires_db

MONTHLY = {
    "frequency": "monthly",
    "due": {"months_after": 1, "day": 11},
    "holiday_rule": "earlier_working_day",
}


@pytest.fixture
def admin(web: TestClient, migrated_db: str) -> Signed:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    return sign_in(web, "boss@firm.in")


def _ok(resp: Any, status: int = 200) -> Any:
    assert resp.status_code == status, resp.text
    return resp.json()


def filing(web: TestClient, admin: Signed, code: str = "GSTR-1", *, publish: bool = True) -> int:
    body = {
        "code": code,
        "name": f"{code} return",
        "default_checklist": [
            {"label": "Sales register"},
            {"label": "Credit notes", "required": False},
        ],
    }
    created = _ok(web.post("/api/admin/filings", json=body, headers=admin.headers), 201)
    if publish:
        url = f"/api/admin/filings/{created['id']}/calendar"
        _ok(web.put(f"{url}/draft", json=MONTHLY, headers=admin.headers))
        _ok(web.post(f"{url}/publish", headers=admin.headers))
    return int(created["id"])


def package(web: TestClient, admin: Signed, name: str = "GST Monthly") -> int:
    created = _ok(web.post("/api/admin/packages", json={"name": name}, headers=admin.headers), 201)
    return int(created["id"])


# --- Done when: a package cannot be published without a filing and a fee; editing creates a
# new version ----------------------------------------------------------------------------------


def test_publishing_needs_a_filing_and_a_fee_and_editing_makes_a_new_version(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    gst = filing(web, admin, "GSTR-1")
    pid = package(web, admin)
    url = f"/api/admin/packages/{pid}"

    refused = web.post(f"{url}/publish", headers=admin.headers)
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "cannot_publish"
    message = refused.json()["error"]["message"]
    assert "Set a fee." in message
    assert "Add at least one filing." in message

    # A fee alone is not enough...
    _ok(
        web.put(
            f"{url}/draft",
            json={"fee": "1500", "billing_frequency": "monthly"},
            headers=admin.headers,
        )
    )
    assert "Add at least one filing." in web.post(f"{url}/publish", headers=admin.headers).text
    # ...nor a filing alone.
    _ok(web.put(f"{url}/draft", json={"filings": [{"filing_id": gst}]}, headers=admin.headers))
    assert "Set a fee." in web.post(f"{url}/publish", headers=admin.headers).text

    draft = {"fee": "1500.50", "billing_frequency": "monthly", "filings": [{"filing_id": gst}]}
    _ok(web.put(f"{url}/draft", json=draft, headers=admin.headers))
    v1 = _ok(web.post(f"{url}/publish", headers=admin.headers))
    assert (v1["version"], v1["status"], v1["fee"]) == (1, "published", "1500.50")

    # Editing a published package starts version 2; version 1 is untouched.
    edited = _ok(web.put(f"{url}/draft", json={**draft, "fee": "1750"}, headers=admin.headers))
    assert (edited["version"], edited["status"], edited["fee"]) == (2, "draft", "1750.00")
    versions = _ok(web.get(url))["versions"]
    assert [(v["version"], v["status"], v["fee"]) for v in versions] == [
        (2, "draft", "1750.00"),
        (1, "published", "1500.50"),
    ]
    _ok(web.post(f"{url}/publish", headers=admin.headers))
    summary = _ok(web.get("/api/admin/packages"))[0]
    assert (summary["current_version"], summary["current_fee"], summary["has_draft"]) == (
        2,
        "1750.00",
        False,
    )
    assert events(migrated_db).count("package.published") == 2


def test_a_published_version_cannot_be_changed_even_in_the_database(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    gst = filing(web, admin)
    pid = package(web, admin)
    url = f"/api/admin/packages/{pid}"
    draft = {"fee": "900", "billing_frequency": "monthly", "filings": [{"filing_id": gst}]}
    _ok(web.put(f"{url}/draft", json=draft, headers=admin.headers))
    _ok(web.post(f"{url}/publish", headers=admin.headers))

    engine = create_engine(migrated_db)
    try:
        for sql in (
            "UPDATE package_versions SET fee_paise = 1",
            "DELETE FROM package_versions",
            "UPDATE package_filings SET position = 9",
            "DELETE FROM package_filings",
            "UPDATE package_checklist_items SET label = 'x'",
            "DELETE FROM package_checklist_items",
            "INSERT INTO package_checklist_items (package_filing_id, position, label, required) "
            "SELECT id, 9, 'sneaked in', 1 FROM package_filings",
            "UPDATE calendar_versions SET due_day = 20",
            "DELETE FROM calendar_versions",
        ):
            with (
                pytest.raises(Exception, match="published versions cannot be changed"),
                engine.begin() as conn,
            ):
                conn.execute(text(sql))
    finally:
        engine.dispose()


def test_each_filing_needs_a_published_calendar_and_retired_ones_are_refused(
    web: TestClient, admin: Signed
) -> None:
    unscheduled = filing(web, admin, "TDS 24Q", publish=False)
    pid = package(web, admin)
    url = f"/api/admin/packages/{pid}"
    body = {"fee": "500", "billing_frequency": "quarterly", "filings": [{"filing_id": unscheduled}]}
    _ok(web.put(f"{url}/draft", json=body, headers=admin.headers))
    assert (
        "TDS 24Q has no published calendar yet."
        in web.post(f"{url}/publish", headers=admin.headers).text
    )

    # Retiring a filing already in the draft blocks publishing until it is removed...
    _ok(
        web.patch(
            f"/api/admin/filings/{unscheduled}", json={"is_active": False}, headers=admin.headers
        )
    )
    assert (
        "TDS 24Q is retired; remove it." in web.post(f"{url}/publish", headers=admin.headers).text
    )
    # ...and a retired filing cannot be newly added anywhere.
    other = package(web, admin, "Other")
    added = web.put(
        f"/api/admin/packages/{other}/draft",
        json={"filings": [{"filing_id": unscheduled}]},
        headers=admin.headers,
    )
    assert added.status_code == 422
    assert added.json()["error"]["code"] == "retired_filing"


def test_a_filing_brings_its_checklist_template_unless_one_is_given(
    web: TestClient, admin: Signed
) -> None:
    gst = filing(web, admin, "GSTR-1")
    itr = filing(web, admin, "ITR")
    pid = package(web, admin)
    draft = _ok(
        web.put(
            f"/api/admin/packages/{pid}/draft",
            json={
                "filings": [
                    {"filing_id": gst},
                    {"filing_id": itr, "checklist": [{"label": "Form 16"}, {"label": "Form 26AS"}]},
                ]
            },
            headers=admin.headers,
        )
    )
    lists = {
        f["code"]: [(i["label"], i["required"]) for i in f["checklist"]] for f in draft["filings"]
    }
    assert lists == {
        "GSTR-1": [("Sales register", True), ("Credit notes", False)],
        "ITR": [("Form 16", True), ("Form 26AS", True)],
    }
    assert [f["code"] for f in draft["filings"]] == ["GSTR-1", "ITR"]  # order kept


@pytest.mark.parametrize(
    ("draft", "where"),
    [
        ({"fee": "0"}, "fee"),
        ({"fee": "-5"}, "fee"),
        ({"fee": "10.555"}, "fee"),
        ({"billing_frequency": "weekly"}, "billing_frequency"),
        ({"filings": [{"filing_id": 1}, {"filing_id": 1}]}, "filings"),
        (
            {"filings": [{"filing_id": 1, "checklist": [{"label": "A"}, {"label": " a "}]}]},
            "filings",
        ),
    ],
)
def test_bad_drafts_are_refused(
    web: TestClient, admin: Signed, draft: dict[str, Any], where: str
) -> None:
    pid = package(web, admin)
    resp = web.put(f"/api/admin/packages/{pid}/draft", json=draft, headers=admin.headers)
    assert resp.status_code == 422
    assert any(where in f["where"] for f in resp.json()["error"]["fields"])


def test_unknown_filings_and_duplicate_names_are_refused(web: TestClient, admin: Signed) -> None:
    pid = package(web, admin, "Same")
    unknown = web.put(
        f"/api/admin/packages/{pid}/draft",
        json={"filings": [{"filing_id": 424242}]},
        headers=admin.headers,
    )
    assert unknown.json()["error"]["code"] == "unknown_filing"
    assert (
        web.post("/api/admin/packages", json={"name": "same"}, headers=admin.headers).status_code
        == 409
    )
    other = package(web, admin, "Other")
    renamed = web.patch(
        f"/api/admin/packages/{other}", json={"name": "SAME"}, headers=admin.headers
    )
    assert renamed.status_code == 409


def test_a_draft_can_be_thrown_away_without_touching_what_is_published(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    gst = filing(web, admin)
    pid = package(web, admin)
    url = f"/api/admin/packages/{pid}"
    draft = {"fee": "900", "billing_frequency": "yearly", "filings": [{"filing_id": gst}]}
    _ok(web.put(f"{url}/draft", json=draft, headers=admin.headers))
    _ok(web.post(f"{url}/publish", headers=admin.headers))
    _ok(web.put(f"{url}/draft", json={**draft, "fee": "1"}, headers=admin.headers))
    assert web.delete(f"{url}/draft", headers=admin.headers).status_code == 204
    assert web.delete(f"{url}/draft", headers=admin.headers).status_code == 404
    assert web.post(f"{url}/publish", headers=admin.headers).status_code == 409
    versions = _ok(web.get(url))["versions"]
    assert [(v["version"], v["fee"]) for v in versions] == [(1, "900.00")]
    assert "package.draft_discarded" in events(migrated_db)


# --- Filing types and calendars --------------------------------------------------------------


def test_calendars_are_versioned_the_same_way(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    fid = filing(web, admin, "TDS 24Q", publish=False)
    url = f"/api/admin/filings/{fid}/calendar"
    assert web.post(f"{url}/publish", headers=admin.headers).status_code == 409
    quarterly = {
        "frequency": "quarterly",
        "period_basis": "financial_year",
        "due": {"months_after": 1, "day": 31, "overrides": {"Q4": {"months_after": 2, "day": 31}}},
        "lead_days": 10,
        "holiday_rule": "as_is",
        "notes": "Q4 is due 31 May",
    }
    _ok(web.put(f"{url}/draft", json=quarterly, headers=admin.headers))
    v1 = _ok(web.post(f"{url}/publish", headers=admin.headers))
    assert v1["due"] == {
        "months_after": 1,
        "day": 31,
        "overrides": {"Q4": {"months_after": 2, "day": 31}},
    }
    _ok(web.put(f"{url}/draft", json={**quarterly, "lead_days": None}, headers=admin.headers))
    detail = _ok(web.get(f"/api/admin/filings/{fid}"))
    assert detail["current"]["version"] == 1
    assert detail["current"]["lead_days"] == 10
    assert detail["draft"]["version"] == 2
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    listed = _ok(web.get("/api/admin/filings"))[0]
    assert (listed["code"], listed["current"]["version"], listed["draft"]["version"]) == (
        "TDS 24Q",
        1,
        2,
    )
    assert events(migrated_db).count("calendar.published") == 1


@pytest.mark.parametrize(
    "calendar",
    [
        {
            **MONTHLY,
            "due": {
                "months_after": 1,
                "day": 11,
                "overrides": {"Q4": {"months_after": 2, "day": 31}},
            },
        },
        {
            **MONTHLY,
            "frequency": "yearly",
            "due": {
                "months_after": 4,
                "day": 31,
                "overrides": {"M1": {"months_after": 1, "day": 1}},
            },
        },
        {**MONTHLY, "due": {"months_after": 13, "day": 11}},
        {**MONTHLY, "due": {"months_after": 1, "day": 32}},
        {**MONTHLY, "lead_days": 91},
        {**MONTHLY, "holiday_rule": "later_working_day"},
    ],
)
def test_impossible_calendars_are_refused(
    web: TestClient, admin: Signed, calendar: dict[str, Any]
) -> None:
    fid = filing(web, admin, publish=False)
    resp = web.put(f"/api/admin/filings/{fid}/calendar/draft", json=calendar, headers=admin.headers)
    assert resp.status_code == 422, resp.text


def test_filing_codes_are_tidied_and_unique(web: TestClient, admin: Signed) -> None:
    created = _ok(
        web.post(
            "/api/admin/filings",
            json={"code": " gstr-3b ", "name": "GSTR-3B"},
            headers=admin.headers,
        ),
        201,
    )
    assert created["code"] == "GSTR-3B"
    assert (
        web.post(
            "/api/admin/filings", json={"code": "GSTR-3B", "name": "x"}, headers=admin.headers
        ).status_code
        == 409
    )
    bad = web.post(
        "/api/admin/filings", json={"code": "GST;DROP", "name": "x"}, headers=admin.headers
    )
    assert bad.status_code == 422
