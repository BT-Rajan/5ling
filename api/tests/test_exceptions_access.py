"""Chunk C37: per-client filing changes (FR-PK-07) and the access review (FR-AC-04).

Done when: a removal stays pending until approved; the access export shows the date generated.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from app.models import Client, ClientFilingChange, Cycle, Email, EmailKind, Filing, Role, Stage
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from tests.audit_helpers import entries
from tests.helpers import Signed, break_glass, db_session, make_user, sign_in_directly
from tests.test_admin_controls import staff_client
from tests.test_assignments import assign, consultant
from tests.test_clients import _ok, admin
from tests.test_tickets import move

__all__ = ["admin"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


def extra_filing(web: TestClient, admin: Signed, code: str = "TDS-26Q") -> int:
    """A published filing that is not in the client's package."""
    filing = _ok(
        web.post(
            "/api/admin/filings",
            json={
                "code": code,
                "name": "TDS return",
                "default_checklist": [{"label": "Challans", "required": True}],
            },
            headers=admin.headers,
        ),
        201,
    )
    cal = f"/api/admin/filings/{filing['id']}/calendar"
    _ok(
        web.put(
            f"{cal}/draft",
            json={"frequency": "quarterly", "due": {"months_after": 1, "day": 31}},
            headers=admin.headers,
        )
    )
    _ok(web.post(f"{cal}/publish", headers=admin.headers))
    return int(filing["id"])


def change(browser: TestClient, signed: Signed, cid: int, filing: int, action: str) -> Any:
    return browser.post(
        f"/api/clients/{cid}/filings",
        json={"filing_id": filing, "action": action, "reason": "Agreed with the client"},
        headers=signed.headers,
    )


def stages(url: str, client_id: int, filing_id: int) -> list[Stage]:
    with db_session(url) as db:
        return list(
            db.scalars(
                select(Cycle.stage)
                .join(Client, Client.id == Cycle.client_pk)
                .where(Client.client_id == client_id, Cycle.filing_id == filing_id)
                .order_by(Cycle.period_start)
            )
        )


# --- LL-145: add at once; a Staff removal waits for an Administrator -------------------------


def test_staff_add_a_filing_for_one_client_and_it_gets_cycles_at_once(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _, sita, sita_s = staff_client(web, admin, migrated_db)
    tds = extra_filing(web, admin)
    view = _ok(sita.get(f"/api/clients/{cid}/filings"))
    assert [f["code"] for f in view["available"]] == ["TDS-26Q"]
    added = _ok(change(sita, sita_s, cid, tds, "add"), 201)
    assert [(f["code"], f["from_package"]) for f in added["filings"]][-1] == ("TDS-26Q", False)
    assert added["changes"][0]["status"] == "approved"  # adding needs no approval
    assert stages(migrated_db, cid, tds)  # its cycles were made straight away
    with db_session(migrated_db) as db:
        labels = db.execute(
            text(
                "SELECT i.label FROM cycle_checklist_items i JOIN cycles c ON c.id = i.cycle_id"
                " WHERE c.filing_id = :f LIMIT 1"
            ),
            {"f": tds},
        ).scalar()
    assert labels == "Challans"  # not in the package: the filing's own checklist
    again = change(sita, sita_s, cid, tds, "add")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_included")


def test_a_staff_removal_stays_pending_and_the_client_keeps_the_filing(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _, sita, sita_s = staff_client(web, admin, migrated_db)
    package_filing = _ok(sita.get(f"/api/clients/{cid}/filings"))["filings"][0]["id"]
    before = stages(migrated_db, cid, package_filing)
    # One cycle has started: it carries on whatever is decided.
    code = _ok(sita.get("/api/tickets", params={"client_id": cid, "limit": 1}))["items"][0]["code"]
    _ok(move(sita, sita_s, code, "request_documents", "scheduled"))

    asked = _ok(change(sita, sita_s, cid, package_filing, "remove"), 201)
    assert asked["filings"][0]["removal_pending"] is True  # still the client's
    assert asked["changes"][0]["status"] == "pending"
    twice = change(sita, sita_s, cid, package_filing, "remove")
    assert (twice.status_code, twice.json()["error"]["code"]) == (409, "already_pending")
    assert Stage.CLOSED not in stages(migrated_db, cid, package_filing)
    assert sita.get("/api/admin/filing-changes").status_code == 403

    waiting = _ok(web.get("/api/admin/filing-changes"))
    assert [(w["client_id"], w["requested_by"]) for w in waiting] == [(cid, "Sita Staff")]
    decision = f"/api/admin/filing-changes/{waiting[0]['id']}/decision"
    bare = web.post(decision, json={"approve": False}, headers=admin.headers)
    assert (bare.status_code, bare.json()["error"]["code"]) == (422, "note_required")
    drafts_before = _client_update_count(migrated_db)
    done = _ok(web.post(decision, json={"approve": True}, headers=admin.headers))
    assert done["status"] == "approved"
    assert len(done["withdrawn"]) == len(before) - 1  # every unstarted cycle closed
    after = stages(migrated_db, cid, package_filing)
    assert after.count(Stage.AWAITING_DOCUMENTS) == 1
    assert after.count(Stage.CLOSED) == len(before) - 1
    assert _client_update_count(migrated_db) == drafts_before  # nothing to tell the client
    late = web.post(decision, json={"approve": False, "note": "No"}, headers=admin.headers)
    assert (late.status_code, late.json()["error"]["code"]) == (409, "already_decided")

    break_glass(web, admin, cid)
    history = _ok(web.get(f"/api/tickets/{done['withdrawn'][0]}"))["history"][0]
    assert (history["action"], history["changed_by"]) == ("withdraw", None)
    assert "Agreed with the client" in history["note"]
    view = _ok(sita.get(f"/api/clients/{cid}/filings"))
    assert package_filing not in [f["id"] for f in view["filings"]]
    events = [e.event for e in entries(migrated_db)]
    assert "client.filing_removal_requested" in events
    assert "client.filing_removed" in events


def _client_update_count(url: str) -> int:
    with db_session(url) as db:
        return int(
            db.scalar(
                select(func.count()).select_from(Email).where(Email.kind == EmailKind.CLIENT_UPDATE)
            )
            or 0
        )


def test_an_administrators_removal_applies_at_once_and_cannot_be_forged(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _, sita, sita_s = staff_client(web, admin, migrated_db)
    filing = _ok(sita.get(f"/api/clients/{cid}/filings"))["filings"][0]["id"]
    code = _ok(sita.get("/api/tickets", params={"client_id": cid, "limit": 1}))["items"][0]["code"]
    # Nobody can close an unstarted cycle by asking for the move directly.
    forged = move(sita, sita_s, code, "withdraw", "scheduled")
    assert (forged.status_code, forged.json()["error"]["code"]) == (409, "removal_not_approved")

    break_glass(web, admin, cid)
    done = _ok(change(web, admin, cid, filing, "remove"), 201)
    assert done["changes"][0]["status"] == "approved"
    assert set(stages(migrated_db, cid, filing)) == {Stage.CLOSED}
    with db_session(migrated_db) as db:
        for sql, message in (
            ("UPDATE client_filing_changes SET reason = 'x'", "decided once"),
            ("DELETE FROM client_filing_changes", "never deleted"),
        ):
            with pytest.raises(DBAPIError, match=message):
                db.execute(text(sql))
            db.rollback()
        assert db.scalar(select(func.count()).select_from(ClientFilingChange)) == 1


def test_only_the_owner_or_an_administrator_changes_a_clients_filings(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _, _, _ = staff_client(web, admin, migrated_db)
    tds = extra_filing(web, admin)
    rid = make_user(migrated_db, "ravi@firm.in", Role.STAFF)
    ravi = TestClient(web.app)
    ravi_s = sign_in_directly(migrated_db, ravi, rid)
    assert change(ravi, ravi_s, cid, tds, "add").status_code == 403
    asked = change(web, admin, cid, tds, "add")  # an Administrator gives a reason first
    assert asked.json()["error"]["code"] == "reason_required"
    with db_session(migrated_db) as db:
        assert db.scalar(select(func.count()).select_from(Filing)) == 2


# --- LL-146: the access review, for the Partners to certify -----------------------------------


def test_the_access_review_lists_everyone_with_access_and_when_it_was_made(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, _, sita, sita_s = staff_client(web, admin, migrated_db)
    code = _ok(sita.get("/api/tickets", params={"client_id": cid, "limit": 1}))["items"][0]["code"]
    _ok(move(sita, sita_s, code, "request_documents", "scheduled"))
    _ok(move(sita, sita_s, code, "documents_received", "awaiting_documents"))
    uid, _, _ = consultant(web, migrated_db)
    _ok(assign(sita, sita_s, code, uid))
    break_glass(web, admin, cid)

    review = _ok(web.get("/api/admin/access-review"))
    assert review["generated_at"]
    access = [(r["client_id"], r["person"], r["access"].split(" ")[0]) for r in review["rows"]]
    assert (cid, "Sita Staff", "Owner") in access
    assert (cid, "Ravi Consultant", "Assigned") in access
    assert (cid, "Test Person", "Break-glass") in access
    assert (None, "Test Person", "Administrator:") in access
    assert sita.get("/api/admin/access-review").status_code == 403

    sheet = web.post(
        "/api/admin/access-review/export", json={"format": "xlsx"}, headers=admin.headers
    )
    assert sheet.status_code == 200
    disposition = sheet.headers["content-disposition"]
    assert "ledgerline-access-review-" in disposition
    assert disposition.endswith('.xlsx"')
    with zipfile.ZipFile(io.BytesIO(sheet.content)) as z:
        xml = z.read("xl/worksheets/sheet1.xml").decode()
    assert "Access review generated " in xml  # the date it was generated, on the sheet itself
    assert "Ravi Consultant" in xml
    paper = web.post(
        "/api/admin/access-review/export", json={"format": "pdf"}, headers=admin.headers
    )
    assert paper.content.startswith(b"%PDF")
    assert b"Access review generated" in paper.content
    logged = [e for e in entries(migrated_db) if e.event == "access_review.exported"]
    assert [e.details["format"] for e in logged] == ["xlsx", "pdf"]
