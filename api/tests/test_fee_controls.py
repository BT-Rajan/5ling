"""Chunk C26: fee approvals and the overdue policy (BRD FR-FE-06, FR-FE-08)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.fees.job import run
from app.timeutil import today_ist
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import events
from tests.conftest import make_settings
from tests.helpers import Signed, db_session
from tests.test_assignments import setup
from tests.test_clients import _ok, admin, publish_package
from tests.test_documents import scanner
from tests.test_fees import at, factory, new_client
from tests.test_filing import file_it, ready_to_file
from tests.test_payments import invoiced, pay, staff
from tests.test_tickets import checker

__all__ = ["admin", "checker", "factory", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


def adjust(browser: TestClient, signed: Signed, invoice: int, kind: str, amount: str) -> Any:
    return browser.post(
        f"/api/invoices/{invoice}/adjustments",
        json={"kind": kind, "amount": amount, "reason": "Agreed with the partner"},
        headers=signed.headers,
    )


def decide(web: TestClient, admin: Signed, adjustment: int, **body: Any) -> Any:
    return web.post(
        f"/api/admin/fee-adjustments/{adjustment}/decision", json=body, headers=admin.headers
    )


# --- LL-109: credit notes, discounts and write-offs wait for an Administrator -----------------


def test_a_pending_write_off_changes_nothing_until_approved(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    sid, sita, sita_s = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    cid, inv = invoiced(web, admin, factory, sid)

    asked = _ok(adjust(sita, sita_s, inv, "write_off", "200"), 201)
    pending = asked["adjustments"][0]
    assert (pending["kind_label"], pending["status"], pending["amount"]) == (
        "Write-off",
        "pending",
        "200.00",
    )
    assert (asked["balance"], asked["ledger"]) == ("1180.00", [])  # no effect yet

    # Staff cannot decide; the Administrator sees it waiting.
    assert sita.get("/api/admin/fee-adjustments").status_code == 403
    assert decide(sita, sita_s, pending["id"], approve=True).status_code == 403
    waiting = _ok(web.get("/api/admin/fee-adjustments"))
    assert [(a["client_id"], a["kind"], a["requested_by"]) for a in waiting] == [
        (cid, "write_off", "Sita Staff")
    ]

    approved = _ok(decide(web, admin, pending["id"], approve=True))
    assert (approved["status"], approved["decided_by"]) == ("approved", "Test Person")
    fees = _ok(sita.get(f"/api/clients/{cid}/fees"))
    assert fees["balance"] == "980.00"
    assert [(e["kind"], e["amount"]) for e in fees["ledger"]] == [("write_off", "200.00")]
    again = decide(web, admin, pending["id"], approve=False, note="Changed my mind")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_decided")
    assert "fee.adjustment_requested" in events(migrated_db)
    assert "fee.adjustment_approved" in events(migrated_db)

    # Paying the rest settles it.
    assert _ok(pay(sita, sita_s, inv, "980"), 201)["status"] == "paid"


def test_rejection_needs_a_reason_and_approval_rechecks_the_balance(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    sid, sita, sita_s = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    cid, inv = invoiced(web, admin, factory, sid)
    too_much = adjust(sita, sita_s, inv, "discount", "1180.01")
    assert (too_much.status_code, too_much.json()["error"]["code"]) == (409, "exceeds_balance")
    first = _ok(adjust(sita, sita_s, inv, "discount", "700"), 201)["adjustments"][0]["id"]
    second = _ok(adjust(sita, sita_s, inv, "credit_note", "700"), 201)["adjustments"][0]["id"]

    bare = decide(web, admin, second, approve=False)
    assert (bare.status_code, bare.json()["error"]["code"]) == (409, "note_required")
    _ok(decide(web, admin, first, approve=True))
    # Both together would take the balance below zero.
    over = decide(web, admin, second, approve=True)
    assert (over.status_code, over.json()["error"]["code"]) == (409, "exceeds_balance")
    no = _ok(decide(web, admin, second, approve=False, note="Discount already given"))
    assert (no["status"], no["decision_note"], no["credit_note_no"]) == (
        "rejected",
        "Discount already given",
        None,
    )
    fees = _ok(sita.get(f"/api/clients/{cid}/fees"))
    assert fees["balance"] == "480.00"
    assert [a["status"] for a in fees["adjustments"]] == ["rejected", "approved"]


def test_credit_notes_get_gapless_numbers_and_an_administrators_apply_at_once(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    cid, inv = invoiced(web, admin, factory, admin.user_id)
    one = _ok(adjust(web, admin, inv, "credit_note", "100"), 201)
    two = _ok(adjust(web, admin, inv, "credit_note", "50.50"), 201)
    numbers = [a["credit_note_no"] for a in two["adjustments"]]
    fy = numbers[0].split("/")[1]
    assert numbers == [f"CN/{fy}/00002", f"CN/{fy}/00001"]
    assert one["adjustments"][0]["status"] == "approved"
    assert two["balance"] == "1029.50"
    assert (
        _ok(adjust(web, admin, inv, "discount", "29.50"), 201)["adjustments"][0]["credit_note_no"]
        is None
    )
    assert cid


def test_the_database_refuses_adjustments_without_approval(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    sid, sita, sita_s = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    _, inv = invoiced(web, admin, factory, sid)
    pending = _ok(adjust(sita, sita_s, inv, "write_off", "100"), 201)["adjustments"][0]["id"]
    for sql, message in (
        (
            "INSERT INTO ledger_entries (client_pk, invoice_id, kind, amount_paise, entry_date,"
            " adjustment_id, created_by_id, created_at) SELECT client_pk, invoice_id, kind,"
            " amount_paise, CURRENT_DATE, id, requested_by_id, requested_at FROM fee_adjustments",
            "approved first",
        ),
        ("DELETE FROM fee_adjustments", "never deleted"),
        ("DELETE FROM fee_policy", "never deleted"),
    ):
        with db_session(migrated_db) as db, pytest.raises(DBAPIError, match=message):
            db.execute(text(sql))
    _ok(decide(web, admin, pending, approve=True))
    with db_session(migrated_db) as db, pytest.raises(DBAPIError, match="decided once"):
        db.execute(text("UPDATE fee_adjustments SET amount_paise = 1"))


def test_only_the_owner_or_an_administrator_asks_for_an_adjustment(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    sid, _, _ = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    _, inv = invoiced(web, admin, factory, sid)
    _, ravi, ravi_s = staff(web, migrated_db, "ravi@firm.in", "Ravi Staff")
    assert adjust(ravi, ravi_s, inv, "write_off", "10").status_code == 403


# --- LL-110, LL-111: the overdue policy and the one-time override ------------------------------


def overdue_client(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> str:
    """A client with ₹1,180 overdue and a cycle ready to file; returns its ticket code."""
    _, code, _ = setup(web, admin, migrated_db)
    # Joined on the 1st of last month, invoiced that day, due 10 days later: overdue now.
    start = (today_ist().replace(day=1) - timedelta(days=1)).replace(day=1)
    with db_session(migrated_db) as db:
        db.execute(text("UPDATE clients SET created_at = :d"), {"d": f"{start} 03:00:00"})
        db.commit()
    assert run(factory, make_settings(invoice_terms_days=10), now=at(start)).raised == 1
    return code


def set_policy(web: TestClient, admin: Signed, mode: str, limit: str = "0") -> Any:
    return web.put(
        "/api/admin/fee-policy", json={"mode": mode, "limit": limit}, headers=admin.headers
    )


def test_warn_by_default_then_block_with_a_one_time_override(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    factory: sessionmaker[Session],
    scanner: object,
) -> None:
    code = overdue_client(web, admin, migrated_db, factory)
    ready_to_file(web, admin, checker, code)
    # BRD Q3: warn by default. Shown on the filing, not refused.
    assert _ok(web.get("/api/admin/fee-policy"))["mode"] == "warn"
    fees = _ok(web.get(f"/api/tickets/{code}"))["cycle"]["fees"]
    assert (fees["mode"], fees["overdue"], fees["limit"], fees["blocked"]) == (
        "warn",
        "1180.00",
        "0.00",
        False,
    )
    assert fees["warning"] == "This client has ₹1180.00 in overdue fees."

    # Reminders only: no warning.
    assert _ok(set_policy(web, admin, "reminder"))["mode"] == "reminder"
    assert _ok(web.get(f"/api/tickets/{code}"))["cycle"]["fees"]["warning"] is None

    # Block, but the client is under the limit: not over it, so nothing.
    _ok(set_policy(web, admin, "block", "1180"))
    assert _ok(web.get(f"/api/tickets/{code}"))["cycle"]["fees"]["blocked"] is False
    # Block over the limit: Filed is refused.
    _ok(set_policy(web, admin, "block", "1000"))
    fees = _ok(web.get(f"/api/tickets/{code}"))["cycle"]["fees"]
    assert fees["blocked"] is True
    assert "blocked" in fees["warning"]
    refused = file_it(web, admin, code, "AA0705260012345")
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "fees_overdue")
    assert "fee.policy_changed" in events(migrated_db)

    # The Administrator overrides it once, with a reason.
    short = web.post(
        f"/api/tickets/{code}/fee-waiver", json={"reason": "ok"}, headers=admin.headers
    )
    assert short.status_code == 422
    waived = _ok(
        web.post(
            f"/api/tickets/{code}/fee-waiver",
            json={"reason": "Client paying on Friday; filing deadline today"},
            headers=admin.headers,
        ),
        201,
    )
    assert waived["cycle"]["fees"]["blocked"] is False
    assert waived["cycle"]["fees"]["waiver"]["granted_by"] == "Test Person"
    twice = web.post(
        f"/api/tickets/{code}/fee-waiver",
        json={"reason": "Once more please"},
        headers=admin.headers,
    )
    assert (twice.status_code, twice.json()["error"]["code"]) == (409, "already_waived")
    assert _ok(file_it(web, admin, code, "AA0705260012345"))["cycle"]["stage"] == "filed"
    assert "fee.filing_waived" in events(migrated_db)


def test_each_client_can_have_its_own_limit(
    web: TestClient,
    admin: Signed,
    migrated_db: str,
    factory: sessionmaker[Session],
) -> None:
    """BRD Q3: the limit is set per client, by its owner or an Administrator, with a reason."""
    code = overdue_client(web, admin, migrated_db, factory)
    cid = _ok(web.get(f"/api/tickets/{code}"))["client"]["client_id"]
    fees = _ok(web.get(f"/api/clients/{cid}/fees"))
    assert (fees["overdue"], fees["overdue_limit"], fees["firm_limit"], fees["over_limit"]) == (
        "1180.00",
        None,
        "0.00",
        True,
    )

    def set_limit(browser: TestClient, signed: Signed, limit: str | None) -> Any:
        return browser.put(
            f"/api/clients/{cid}/overdue-limit",
            json={"limit": limit, "reason": "Agreed credit with the partner"},
            headers=signed.headers,
        )

    raised = _ok(set_limit(web, admin, "2000"))
    assert (raised["overdue_limit"], raised["over_limit"]) == ("2000.00", False)
    fees = _ok(web.get(f"/api/tickets/{code}"))["cycle"]["fees"]
    assert (fees["limit"], fees["warning"]) == ("2000.00", None)
    # Even with the block on, this client is within its own limit.
    _ok(set_policy(web, admin, "block"))
    assert _ok(web.get(f"/api/tickets/{code}"))["cycle"]["fees"]["blocked"] is False

    same = set_limit(web, admin, "2000")
    assert (same.status_code, same.json()["error"]["code"]) == (409, "no_change")
    no_reason = web.put(
        f"/api/clients/{cid}/overdue-limit", json={"limit": "5"}, headers=admin.headers
    )
    assert no_reason.status_code == 422
    assert set_limit(web, admin, "-1").status_code == 422

    # Back to the firm's limit: over it again.
    back = _ok(set_limit(web, admin, None))
    assert (back["overdue_limit"], back["over_limit"]) == (None, True)
    history = _ok(web.get(f"/api/clients/{cid}/history"))
    assert [(h["field"], h["old"], h["new"]) for h in history[:2]] == [
        ("overdue_limit", "₹2000.00", "the firm's limit"),
        ("overdue_limit", "the firm's limit", "₹2000.00"),
    ]
    assert "client.overdue_limit_changed" in events(migrated_db)


def test_only_the_owner_or_an_administrator_sets_a_clients_limit(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    sid, sita, sita_s = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    pid = publish_package(web, admin)
    cid = new_client(web, admin, pid, 1, owner_id=sid)
    body = {"limit": "500", "reason": "Long-standing client"}
    assert (
        _ok(sita.put(f"/api/clients/{cid}/overdue-limit", json=body, headers=sita_s.headers))[
            "overdue_limit"
        ]
        == "500.00"
    )
    _, ravi, ravi_s = staff(web, migrated_db, "ravi@firm.in", "Ravi Staff")
    assert (
        ravi.put(f"/api/clients/{cid}/overdue-limit", json=body, headers=ravi_s.headers).status_code
        == 403
    )


def test_paying_lifts_the_block(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    factory: sessionmaker[Session],
    scanner: object,
) -> None:
    code = overdue_client(web, admin, migrated_db, factory)
    ready_to_file(web, admin, checker, code)
    _ok(set_policy(web, admin, "block"))
    cid = _ok(web.get(f"/api/tickets/{code}"))["client"]["client_id"]
    inv = _ok(web.get(f"/api/clients/{cid}/fees"))["invoices"][-1]["id"]
    _ok(pay(web, admin, inv, "1180"), 201)
    assert _ok(file_it(web, admin, code, "AA0705260012345"))["cycle"]["stage"] == "filed"


def test_only_administrators_set_the_policy_or_override(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    sid, sita, sita_s = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    pid = publish_package(web, admin)
    new_client(web, admin, pid, 1, owner_id=sid)
    assert sita.get("/api/admin/fee-policy").status_code == 403
    assert (
        sita.put(
            "/api/admin/fee-policy", json={"mode": "block", "limit": "0"}, headers=sita_s.headers
        ).status_code
        == 403
    )
    code = _ok(sita.get("/api/tickets", params={"limit": 1}))["items"][0]["code"]
    assert (
        sita.post(
            f"/api/tickets/{code}/fee-waiver",
            json={"reason": "Please let me"},
            headers=sita_s.headers,
        ).status_code
        == 403
    )
    for bad in ({"mode": "nag", "limit": "0"}, {"mode": "block", "limit": "-1"}):
        assert web.put("/api/admin/fee-policy", json=bad, headers=admin.headers).status_code == 422
