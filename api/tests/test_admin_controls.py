"""Chunk C29: rules as data and break-glass access (BRD NFR-MN-01, FR-AC-05)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.auth.breakglass import active_grant
from app.models import Client, Role
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session, make_user, sign_in, sign_in_directly
from tests.test_clients import _ok, admin, client_body, create, publish_package
from tests.test_idle import idle_for

__all__ = ["admin"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db

DEFAULTS = {
    "waiting_grace_days": 3,
    "reminder_interval_days": 2,
    "escalation_days_before": 2,
    "portal_link_hours": 72,
    "idle_timeout_minutes": 20,
    "break_glass_minutes": 240,
    "fee_reminder_interval_days": 7,
}


def staff_client(web: TestClient, admin: Signed, url: str) -> tuple[int, int, TestClient, Signed]:
    """A client owned by a Staff member; returns (client id, staff id, staff browser, signed)."""
    sid = make_user(url, "sita@firm.in", Role.STAFF, name="Sita Staff")
    sita = TestClient(web.app)
    sita_s = sign_in_directly(url, sita, sid)
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=sid)), 201)["client_id"]
    return cid, sid, sita, sita_s


def break_glass(web: TestClient, admin: Signed, cid: int, reason: str = "Sita is on leave") -> Any:
    return web.post(
        f"/api/clients/{cid}/break-glass", json={"reason": reason}, headers=admin.headers
    )


# --- LL-121: break-glass access with a reason, flagged in the audit log --------------------------


def test_an_administrator_gives_a_reason_before_opening_someone_elses_client(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _, sita, _ = staff_client(web, admin, migrated_db)
    code = _ok(sita.get("/api/tickets", params={"client_id": cid, "limit": 1}))["items"][0]["code"]

    # Lists are not opening a client.
    assert cid in [c["client_id"] for c in _ok(web.get("/api/clients"))["items"]]
    # Opening it, or anything of it, asks for a reason first.
    for path in (
        f"/api/clients/{cid}",
        f"/api/clients/{cid}/history",
        f"/api/clients/{cid}/fees",
        f"/api/tickets/{code}",
        f"/api/tickets/{code}/documents",
    ):
        refused = web.get(path)
        assert refused.status_code == 403, path
        assert refused.json()["error"]["code"] == "reason_required", path
        assert refused.json()["error"]["client_id"] == cid
    assert break_glass(web, admin, cid, "no").status_code == 422  # a real reason

    opened = _ok(break_glass(web, admin, cid), 201)
    assert (opened["client_id"], opened["reason"], opened["active"]) == (
        cid,
        "Sita is on leave",
        True,
    )
    assert _ok(web.get(f"/api/clients/{cid}"))["client_id"] == cid
    assert _ok(web.get(f"/api/tickets/{code}"))["code"] == code

    logged = [e for e in entries(migrated_db) if e.event == "client.break_glass"]
    assert len(logged) == 1
    assert logged[0].details["flagged"] is True
    assert logged[0].details["reason"] == "Sita is on leave"
    assert logged[0].target_id == str(cid)
    review = _ok(web.get("/api/admin/break-glass"))
    assert [(g["client_id"], g["administrator"]) for g in review] == [(cid, "Test Person")]

    # The owner needs no reason; nor does the Administrator for their own clients.
    assert _ok(sita.get(f"/api/clients/{cid}"))["client_id"] == cid
    own = _ok(
        create(
            web,
            admin,
            client_body(
                publish_package(web, admin, "Other"), owner_id=admin.user_id, pan="ABCPE9999F"
            ),
        ),
        201,
    )
    assert break_glass(web, admin, own["client_id"]).status_code == 409


def test_a_grant_lasts_only_its_time_and_is_never_changed(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _, _, _ = staff_client(web, admin, migrated_db)
    _ok(break_glass(web, admin, cid), 201)
    with db_session(migrated_db) as db:
        for sql, message in (
            ("UPDATE break_glass_grants SET reason = 'x'", "never changed"),
            ("DELETE FROM break_glass_grants", "never deleted"),
        ):
            with pytest.raises(DBAPIError, match=message):
                db.execute(text(sql))
            db.rollback()
    # It runs out after the rule's time (240 minutes by default).
    with db_session(migrated_db) as db:
        pk = db.scalar(select(Client.id).where(Client.client_id == cid))
        assert pk is not None
        now = utcnow()
        assert active_grant(db, admin.user_id, pk, now=now + timedelta(minutes=239)) is not None
        assert active_grant(db, admin.user_id, pk, now=now + timedelta(minutes=241)) is None


def test_staff_cannot_break_glass(web: TestClient, admin: Signed, migrated_db: str) -> None:
    cid, _, _, _ = staff_client(web, admin, migrated_db)
    rid = make_user(migrated_db, "ravi@firm.in", Role.STAFF, name="Ravi Staff")
    ravi = TestClient(web.app)
    ravi_s = sign_in_directly(migrated_db, ravi, rid)
    assert break_glass(ravi, ravi_s, cid).status_code == 403
    assert ravi.get(f"/api/clients/{cid}").status_code == 403


# --- LL-122: rules are data an Administrator edits; no release needed -------------------------


def test_an_administrator_changes_the_rules_and_they_apply_at_once(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    current = _ok(web.get("/api/admin/rules"))
    assert {k: current[k] for k in DEFAULTS} == DEFAULTS
    assert current["limits"]["idle_timeout_minutes"] == {"default": 20, "min": 5, "max": 240}

    same = web.put("/api/admin/rules", json=DEFAULTS, headers=admin.headers)
    assert (same.status_code, same.json()["error"]["code"]) == (409, "no_change")
    for bad in (
        {"idle_timeout_minutes": 2},
        {"portal_link_hours": 721},
        {"reminder_interval_days": 0},
    ):
        assert (
            web.put("/api/admin/rules", json=DEFAULTS | bad, headers=admin.headers).status_code
            == 422
        )
    changed = _ok(
        web.put(
            "/api/admin/rules",
            json=DEFAULTS | {"idle_timeout_minutes": 45, "portal_link_hours": 24},
            headers=admin.headers,
        )
    )
    assert (changed["idle_timeout_minutes"], changed["updated_by"]) == (45, "Test Person")
    log = [e for e in entries(migrated_db) if e.event == "rules.changed"]
    assert log[-1].details == {"idle_timeout_minutes": "20 -> 45", "portal_link_hours": "72 -> 24"}

    # The new idle timeout applies at once: 30 minutes idle is now fine, 46 is not.
    make_user(migrated_db, "someone@firm.in", Role.STAFF)
    browser = TestClient(web.app)
    sign_in(browser, "someone@firm.in")
    idle_for(migrated_db, 30 * 60)
    assert browser.get("/api/auth/me").status_code == 200
    idle_for(migrated_db, 46 * 60)
    gone = browser.get("/api/auth/me")
    assert gone.status_code == 401
    assert "45 minutes" in gone.json()["error"]["message"]


def test_staff_cannot_see_or_change_the_rules(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF)
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    assert sita.get("/api/admin/rules").status_code == 403
    assert sita.put("/api/admin/rules", json=DEFAULTS, headers=sita_s.headers).status_code == 403
    with db_session(migrated_db) as db, pytest.raises(DBAPIError, match="never deleted"):
        db.execute(text("DELETE FROM rule_settings"))
