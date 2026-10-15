"""Chunk C25: the payments ledger and fee status (BRD FR-FE-03, FR-FE-04, FR-FE-05)."""

from __future__ import annotations

import threading
from datetime import date, timedelta
from typing import Any

import pytest
from app.fees.job import run
from app.fees.ledger import FeeStatus, invoice_status, most_pressing
from app.models import Role
from app.timeutil import today_ist
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import events
from tests.conftest import make_settings
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_clients import _ok, admin, publish_package
from tests.test_fees import at, factory, new_client

__all__ = ["admin", "factory"]  # fixtures re-used from other test modules

D = date(2026, 5, 10)


def test_invoice_status() -> None:
    assert invoice_status(1000, 0, D, D) is FeeStatus.DUE  # due today: not yet overdue
    assert invoice_status(1000, 400, D, D) is FeeStatus.PART_PAID
    assert invoice_status(1000, 1000, D, D) is FeeStatus.PAID
    assert invoice_status(1000, 0, D, D + timedelta(days=1)) is FeeStatus.OVERDUE
    assert invoice_status(1000, 400, D, D + timedelta(days=1)) is FeeStatus.OVERDUE
    assert invoice_status(1000, 1000, D, D + timedelta(days=99)) is FeeStatus.PAID
    assert (
        most_pressing([FeeStatus.PAID, FeeStatus.DUE, FeeStatus.PART_PAID]) is FeeStatus.PART_PAID
    )
    assert most_pressing([FeeStatus.PAID, FeeStatus.OVERDUE]) is FeeStatus.OVERDUE
    assert most_pressing([FeeStatus.PAID]) is FeeStatus.PAID
    assert most_pressing([]) is None


pytestmark_db = pytest.mark.requires_db


def staff(web: TestClient, url: str, email: str, name: str) -> tuple[int, TestClient, Signed]:
    uid = make_user(url, email, Role.STAFF, name=name)
    browser = TestClient(web.app)
    return uid, browser, sign_in_directly(url, browser, uid)


def invoiced(
    web: TestClient, admin: Signed, factory: sessionmaker[Session], owner_id: int
) -> tuple[int, int]:
    """A client of `owner_id` on ₹1,000 a month with this month's invoice (₹1,180 with GST)."""
    pid = publish_package(web, admin)
    cid = new_client(web, admin, pid, 1, owner_id=owner_id)
    assert run(factory, make_settings(), now=at(today_ist())).raised == 1
    fees = _ok(web.get(f"/api/clients/{cid}/fees"))
    return cid, int(fees["invoices"][0]["id"])


def pay(browser: TestClient, signed: Signed, invoice: int, amount: str, **over: Any) -> Any:
    body = {
        "amount": amount,
        "paid_on": today_ist().isoformat(),
        "mode": "upi",
        "reference": "UPI-1",
    }
    body.update(over)
    return browser.post(f"/api/invoices/{invoice}/payments", json=body, headers=signed.headers)


# --- LL-104, LL-106: payments, part payments, and status the moment they are recorded ---------


@pytestmark_db
def test_part_payments_then_full_payment_update_the_status_at_once(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    sid, sita, sita_s = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    cid, inv = invoiced(web, admin, factory, sid)

    fees = _ok(sita.get(f"/api/clients/{cid}/fees"))
    assert (fees["status"], fees["balance"]) == ("due", "1180.00")
    assert _ok(sita.get("/api/clients"))["items"][0]["fee_status"] == "due"

    part = _ok(pay(sita, sita_s, inv, "500", mode="cheque", reference="CHQ 000123"), 201)
    assert (part["status"], part["status_label"], part["balance"]) == (
        "part_paid",
        "Part paid",
        "680.00",
    )
    invoice = part["invoices"][0]
    assert (invoice["paid"], invoice["balance"], invoice["status"]) == (
        "500.00",
        "680.00",
        "part_paid",
    )
    entry = part["ledger"][0]
    assert (entry["kind"], entry["amount"], entry["mode"], entry["reference"], entry["by"]) == (
        "payment",
        "500.00",
        "cheque",
        "CHQ 000123",
        "Sita Staff",
    )
    # The client list and the ticket show it straight away.
    assert _ok(sita.get("/api/clients"))["items"][0]["fee_status"] == "part_paid"
    code = _ok(sita.get("/api/tickets", params={"client_id": cid, "limit": 1}))["items"][0]["code"]
    assert _ok(sita.get(f"/api/tickets/{code}"))["fee_status"] == "part_paid"

    too_much = pay(sita, sita_s, inv, "680.01")
    assert (too_much.status_code, too_much.json()["error"]["code"]) == (409, "overpayment")
    future = pay(sita, sita_s, inv, "1", paid_on=(today_ist() + timedelta(days=1)).isoformat())
    assert (future.status_code, future.json()["error"]["code"]) == (409, "future_date")
    for bad in ({"amount": "0"}, {"amount": "-5"}, {"amount": "1.005"}, {"mode": "barter"}):
        assert pay(sita, sita_s, inv, **({"amount": "1"} | bad)).status_code == 422, bad

    done = _ok(pay(sita, sita_s, inv, "680"), 201)
    assert (done["status"], done["balance"]) == ("paid", "0.00")
    again = pay(sita, sita_s, inv, "1")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_paid")
    assert events(migrated_db).count("payment.recorded") == 2


# --- LL-105: reversing entries; originals stay visible ----------------------------------------


@pytestmark_db
def test_a_reversal_restores_the_balance_and_both_entries_stay(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    cid, inv = invoiced(web, admin, factory, admin.user_id)
    _ok(pay(web, admin, inv, "500"), 201)
    paid = _ok(pay(web, admin, inv, "680", reference="UPI-WRONG"), 201)
    wrong = paid["ledger"][0]["id"]

    short = web.post(f"/api/ledger/{wrong}/reverse", json={"reason": "no"}, headers=admin.headers)
    assert short.status_code == 422  # a reason is needed
    fixed = _ok(
        web.post(
            f"/api/ledger/{wrong}/reverse",
            json={"reason": "Bounced: bank returned it"},
            headers=admin.headers,
        ),
        201,
    )
    # The balance after the reversal is the expected figure: 1,180 - 500 - 680 + 680.
    assert (fixed["status"], fixed["balance"]) == ("part_paid", "680.00")
    assert [(e["kind"], e["amount"], e["reversed"]) for e in fixed["ledger"]] == [
        ("reversal", "680.00", False),
        ("payment", "680.00", True),  # the original, still shown
        ("payment", "500.00", False),
    ]
    assert fixed["ledger"][0]["reverses_id"] == wrong
    assert fixed["ledger"][0]["note"] == "Bounced: bank returned it"

    twice = web.post(
        f"/api/ledger/{wrong}/reverse", json={"reason": "Again, by mistake"}, headers=admin.headers
    )
    assert (twice.status_code, twice.json()["error"]["code"]) == (409, "already_reversed")
    of_reversal = web.post(
        f"/api/ledger/{fixed['ledger'][0]['id']}/reverse",
        json={"reason": "Reverse the reversal"},
        headers=admin.headers,
    )
    assert (of_reversal.status_code, of_reversal.json()["error"]["code"]) == (409, "not_a_payment")
    # Paying again after the reversal works, up to the balance.
    assert _ok(pay(web, admin, inv, "680"), 201)["status"] == "paid"
    assert "payment.reversed" in events(migrated_db)
    assert cid


@pytestmark_db
def test_the_database_keeps_the_ledger_append_only(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, inv = invoiced(web, admin, factory, admin.user_id)
    _ok(pay(web, admin, inv, "500"), 201)
    for sql, message in (
        ("UPDATE ledger_entries SET amount_paise = 1", "never changed"),
        ("DELETE FROM ledger_entries", "never deleted"),
        # A reversal that does not match its payment.
        (
            "INSERT INTO ledger_entries (client_pk, invoice_id, kind, amount_paise, entry_date,"
            " reverses_id, created_by_id, created_at) SELECT client_pk, invoice_id, 'reversal',"
            " amount_paise + 1, entry_date, id, created_by_id, created_at FROM ledger_entries",
            "must match",
        ),
    ):
        with db_session(migrated_db) as db, pytest.raises(DBAPIError, match=message):
            db.execute(text(sql))


@pytestmark_db
def test_two_people_paying_at_once_cannot_overpay(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    _, inv = invoiced(web, admin, factory, admin.user_id)
    results: list[int] = []
    barrier = threading.Barrier(2)

    def go() -> None:
        browser = TestClient(web.app)
        signed = sign_in_directly(migrated_db, browser, admin.user_id)
        barrier.wait(timeout=30)
        results.append(pay(browser, signed, inv, "1180").status_code)

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert sorted(results) == [201, 409]


@pytestmark_db
def test_an_unpaid_invoice_past_its_due_date_is_overdue(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    cid = new_client(web, admin, pid, 1)
    with db_session(migrated_db) as db:
        db.execute(text("UPDATE clients SET created_at = created_at - INTERVAL 60 DAY"))
        db.commit()
    # Raised 40 days ago, due 10 days later: now overdue. This month's is merely due.
    run(factory, make_settings(invoice_terms_days=10), now=at(today_ist() - timedelta(days=40)))
    run(factory, make_settings(invoice_terms_days=10), now=at(today_ist()))
    fees = _ok(web.get(f"/api/clients/{cid}/fees"))
    assert fees["status"] == "overdue"
    assert "overdue" in [i["status"] for i in fees["invoices"]]
    assert _ok(web.get("/api/clients"))["items"][0]["fee_status"] == "overdue"


# --- LL-107: fees only for the client's owner and Administrators -------------------------------


@pytestmark_db
def test_fees_are_shown_and_recorded_only_by_the_owner_and_administrators(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    sid, sita, sita_s = staff(web, migrated_db, "sita@firm.in", "Sita Staff")
    cid, inv = invoiced(web, admin, factory, sid)
    entry = _ok(pay(sita, sita_s, inv, "100"), 201)["ledger"][0]["id"]

    rid, ravi, ravi_s = staff(web, migrated_db, "ravi@firm.in", "Ravi Staff")
    assert ravi.get(f"/api/clients/{cid}/fees").status_code == 403
    assert pay(ravi, ravi_s, inv, "100").status_code == 403
    assert (
        ravi.post(
            f"/api/ledger/{entry}/reverse",
            json={"reason": "Not mine to do"},
            headers=ravi_s.headers,
        ).status_code
        == 403
    )
    assert pay(ravi, ravi_s, 999_999, "100").status_code == 404
    assert _ok(ravi.get("/api/clients"))["items"] == []

    # An in-house assignee sees the ticket, not the client's fees.
    code = _ok(web.get("/api/tickets", params={"client_id": cid, "limit": 1}))["items"][0]["code"]
    for action, expected in (
        ("request_documents", "scheduled"),
        ("documents_received", "awaiting_documents"),
    ):
        _ok(
            web.post(
                f"/api/tickets/{code}/transition",
                json={"action": action, "expected_stage": expected},
                headers=admin.headers,
            )
        )
    _ok(
        web.post(
            f"/api/tickets/{code}/assignment", json={"assignee_id": rid}, headers=admin.headers
        )
    )
    seen = _ok(ravi.get(f"/api/tickets/{code}"))
    assert seen["fee_status"] is None
    assert _ok(web.get(f"/api/tickets/{code}"))["fee_status"] == "part_paid"
    # The Administrator sees and records for every client.
    assert _ok(pay(web, admin, inv, "100"), 201)["balance"] == "980.00"
