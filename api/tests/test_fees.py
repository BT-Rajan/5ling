"""Chunk C24: fee plans, gapless invoices and tax (BRD FR-FE-01, FR-FE-02, FR-FE-09)."""

from __future__ import annotations

import threading
from datetime import UTC, date, datetime, timedelta

import pytest
from app.fees.invoices import next_number
from app.fees.job import invoice_client, run
from app.fees.periods import period_containing, periods_due
from app.fees.tax import half_up, is_inter_state, tax_for
from app.models import BillingFrequency, Client, Invoice, InvoiceSeries, Role
from app.timeutil import today_ist
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import events
from tests.conftest import make_settings
from tests.helpers import Signed, break_glass, db_session, make_user, sign_in_directly
from tests.test_alerts import factory
from tests.test_clients import (
    COMPANY_GSTIN,
    FIRM_GSTIN,
    _ok,
    admin,
    client_body,
    create,
    publish_package,
)

__all__ = ["admin", "factory"]  # fixtures re-used from other test modules

M = BillingFrequency.MONTHLY
Q = BillingFrequency.QUARTERLY
Y = BillingFrequency.YEARLY


# --- LL-101: tax by configurable rates; totals match the test set ----------------------------


def test_rounding_is_half_up_to_the_paise() -> None:
    assert [half_up(n, 2) for n in (0, 1, 2, 3, 4, 5)] == [0, 1, 1, 2, 2, 3]
    with pytest.raises(ValueError, match="non-negative"):
        half_up(-1, 2)


@pytest.mark.parametrize(
    ("amount", "rate_bp", "inter", "expected"),
    [
        # (CGST, SGST, IGST, total), in paise
        (1_500_000, 1800, False, (135_000, 135_000, 0, 1_770_000)),  # ₹15,000 at 18%
        (1_500_000, 1800, True, (0, 0, 270_000, 1_770_000)),
        (99_999, 1800, False, (9_000, 9_000, 0, 117_999)),  # 8,999.91 rounds up per line
        (99_999, 1800, True, (0, 0, 18_000, 117_999)),  # 17,999.82
        (50, 1800, False, (5, 5, 0, 60)),  # 4.5 paise each: half up
        (50, 1800, True, (0, 0, 9, 59)),
        (123_456, 500, False, (3_086, 3_086, 0, 129_628)),  # 5%: 3,086.4 each
        (100_000, 0, False, (0, 0, 0, 100_000)),  # a firm not charging GST
        (1, 1800, False, (0, 0, 0, 1)),  # 0.09 paise rounds down
    ],
)
def test_invoice_totals_match_the_test_set(
    amount: int, rate_bp: int, inter: bool, expected: tuple[int, int, int, int]
) -> None:
    tax = tax_for(amount, rate_bp, inter_state=inter)
    assert (tax.cgst, tax.sgst, tax.igst, amount + tax.total) == expected


def test_igst_only_for_a_client_registered_in_another_state() -> None:
    assert is_inter_state("33", COMPANY_GSTIN) is True  # Karnataka client, Tamil Nadu firm
    assert is_inter_state("29", COMPANY_GSTIN) is False
    assert is_inter_state("33", None) is False  # unregistered: taxed where the firm is
    assert is_inter_state(None, COMPANY_GSTIN) is False  # firm state not set


def test_settings_refuse_bad_invoice_settings() -> None:
    for bad in ({"invoice_prefix": "INVOICE"}, {"invoice_prefix": "in"}, {"firm_gst_state": "40"}):
        with pytest.raises(ValueError, match=r"invoice_prefix|firm_gst_state"):
            make_settings(**bad)
    assert make_settings(firm_gst_state="97").firm_gst_state == "97"


# --- LL-102: billing periods follow the financial year -------------------------------------


def test_billing_periods() -> None:
    may = period_containing(M, date(2026, 5, 17))
    assert (may.key, may.label, may.start, may.end) == (
        "M2026-05",
        "May 2026",
        date(2026, 5, 1),
        date(2026, 5, 31),
    )
    assert period_containing(M, date(2028, 2, 3)).end == date(2028, 2, 29)
    q4 = period_containing(Q, date(2027, 2, 10))
    assert (q4.key, q4.label, q4.start, q4.end) == (
        "Q26-27-4",
        "Jan-Mar 2027",
        date(2027, 1, 1),
        date(2027, 3, 31),
    )
    assert period_containing(Q, date(2026, 4, 1)).key == "Q26-27-1"
    assert period_containing(Q, date(2026, 12, 31)).label == "Oct-Dec 2026"
    fy = period_containing(Y, date(2026, 3, 31))
    assert (fy.key, fy.label, fy.start, fy.end) == (
        "Y25-26",
        "FY 2025-26",
        date(2025, 4, 1),
        date(2026, 3, 31),
    )
    keys = [p.key for p in periods_due(M, date(2026, 1, 15), date(2026, 4, 2))]
    assert keys == ["M2026-01", "M2026-02", "M2026-03", "M2026-04"]
    assert [p.key for p in periods_due(Q, date(2026, 3, 20), date(2026, 4, 1))] == [
        "Q25-26-4",
        "Q26-27-1",
    ]
    assert [
        p.key for p in periods_due(BillingFrequency.ONE_TIME, date(2026, 1, 1), date(2027, 1, 1))
    ] == ["ONCE"]
    assert periods_due(M, date(2026, 5, 1), date(2026, 4, 30)) == []


# --- With a database ---------------------------------------------------------------------------

pytestmark_db = pytest.mark.requires_db


def new_client(web: TestClient, admin: Signed, pid: int, n: int, **over: object) -> int:
    body = client_body(
        pid,
        owner_id=over.pop("owner_id", admin.user_id),
        legal_name=f"Client {n}",
        pan=f"ABCPE{1000 + n}F",
        contacts=[{"name": "C", "email": f"c{n}@example.in", "is_primary": True}],
    )
    body.update(over)
    return int(_ok(create(web, admin, body), 201)["client_id"])


def pk_of(url: str, client_id: int) -> int:
    with db_session(url) as db:
        pk = db.scalar(select(Client.id).where(Client.client_id == client_id))
        assert pk is not None
        return int(pk)


def at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 3, 0, tzinfo=UTC)  # 08:30 India time


def invoices(url: str) -> list[Invoice]:
    with db_session(url) as db:
        rows = list(db.scalars(select(Invoice).order_by(Invoice.id)))
        for r in rows:
            db.expunge(r)
        return rows


# --- LL-100, LL-102: gapless invoice numbers; the scheduler ------------------------------------


@pytestmark_db
def test_the_scheduler_raises_each_period_once_and_catches_up(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)  # ₹1,000 a month
    new_client(web, admin, pid, 1)
    settings = make_settings(invoice_prefix="LL", invoice_terms_days=10)
    today = today_ist()

    first = run(factory, settings, now=at(today))
    assert (first.raised, first.failed) == (1, 0)
    assert run(factory, settings, now=at(today)).raised == 0  # safe to run twice

    # Two months without a run: the next run raises both missed months.
    later = today + timedelta(days=62)
    assert run(factory, settings, now=at(later)).raised == 2
    rows = invoices(migrated_db)
    assert [r.seq for r in rows] == [1, 2, 3]
    fy = rows[0].invoice_no.split("/")[1]
    assert rows[0].invoice_no == f"LL/{fy}/00001"
    assert len({r.period_key for r in rows}) == 3
    assert rows[0].due_date == rows[0].issue_date + timedelta(days=10)
    # Unregistered client, no firm state: CGST and SGST at 18% on ₹1,000.
    assert (rows[0].amount_paise, rows[0].cgst_paise, rows[0].sgst_paise, rows[0].igst_paise) == (
        100_000,
        9_000,
        9_000,
        0,
    )
    assert rows[0].total_paise == 118_000
    assert events(migrated_db).count("invoice.raised") == 3


@pytestmark_db
def test_paused_and_closed_clients_are_not_invoiced(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    cid = new_client(web, admin, pid, 1)
    with db_session(migrated_db) as db:
        db.execute(text("UPDATE clients SET status = 'paused' WHERE client_id = :c"), {"c": cid})
        db.commit()
    assert run(factory, make_settings(), now=at(today_ist())).raised == 0


@pytestmark_db
def test_numbers_have_no_gaps_and_no_repeats_under_concurrency(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    pks = [pk_of(migrated_db, new_client(web, admin, pid, n)) for n in range(1, 9)]
    settings = make_settings()
    today = today_ist()
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(pks))

    def bill(pk: int) -> None:
        try:
            with factory() as db:
                barrier.wait(timeout=30)
                invoice_client(db, settings, pk, today)
                db.commit()
        except BaseException as exc:  # reported below
            errors.append(exc)

    threads = [threading.Thread(target=bill, args=(pk,)) for pk in pks]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert errors == []
    rows = invoices(migrated_db)
    assert sorted(r.seq for r in rows) == list(range(1, len(pks) + 1))
    assert len({r.invoice_no for r in rows}) == len(pks)


@pytestmark_db
def test_a_rolled_back_invoice_gives_its_number_back(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    pk = pk_of(migrated_db, new_client(web, admin, pid, 1))
    today = today_ist()
    with factory() as db:
        _, seq, _ = next_number(db, "INV", today)
        assert seq == 1
        db.rollback()  # the invoice transaction failed after taking a number
    with factory() as db:
        invoice_client(db, make_settings(), pk, today)
        db.commit()
    assert [r.seq for r in invoices(migrated_db)] == [1]  # not skipped


@pytestmark_db
def test_the_database_keeps_invoices_and_their_numbering(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid, 1)
    run(factory, make_settings(), now=at(today_ist()))
    for sql, message in (
        ("UPDATE invoices SET total_paise = total_paise + 100", "never changed"),
        ("DELETE FROM invoices", "never deleted"),
        ("UPDATE invoice_series SET next_seq = next_seq + 2", "only goes up by one"),
        ("UPDATE invoice_series SET next_seq = next_seq - 1", "only goes up by one"),
        ("DELETE FROM invoice_series", "never deleted"),
        ("UPDATE tax_rates SET rate_bp = 1200", "never changed"),
        # A number the counter did not just issue (a skip) is refused.
        (
            "INSERT INTO invoices (invoice_no, series_id, seq, client_pk, period_key, period_label,"
            " issue_date, due_date, description, package_version_id, tax_rate_id, tax_rate_bp,"
            " amount_paise, cgst_paise, sgst_paise, igst_paise, total_paise, created_at)"
            " SELECT 'INV/X/00009', series_id, seq + 8, client_pk, 'other', period_label,"
            " issue_date, due_date, description, package_version_id, tax_rate_id, tax_rate_bp,"
            " amount_paise, cgst_paise, sgst_paise, igst_paise, total_paise, created_at"
            " FROM invoices",
            "number just issued",
        ),
    ):
        with db_session(migrated_db) as db, pytest.raises(DBAPIError, match=message):
            db.execute(text(sql))
    with db_session(migrated_db) as db:
        assert db.scalar(select(InvoiceSeries.next_seq)) == 2


@pytestmark_db
def test_igst_for_a_client_registered_in_another_state(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    for n, gstin in ((1, FIRM_GSTIN), (2, COMPANY_GSTIN)):  # Maharashtra, Karnataka
        new_client(web, admin, pid, n, kind="business", pan=None, gstin=gstin)
    run(factory, make_settings(firm_gst_state="27"), now=at(today_ist()))
    rows = {r.client_pk: r for r in invoices(migrated_db)}
    same, other = (rows[k] for k in sorted(rows))
    assert (same.cgst_paise, same.sgst_paise, same.igst_paise) == (9_000, 9_000, 0)
    assert (other.cgst_paise, other.sgst_paise, other.igst_paise) == (0, 0, 18_000)


# --- LL-099: fee plan from the package; overrides need an Administrator ---------------------


@pytestmark_db
def test_staff_ask_for_a_client_fee_and_an_administrator_approves_it(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF, name="Sita Staff")
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    cid = new_client(web, admin, pid, 1, owner_id=sid)

    fees = _ok(sita.get(f"/api/clients/{cid}/fees"))
    assert fees["plan"] | {"override": None} == {
        "package_name": "GST Monthly",
        "package_version": 1,
        "frequency": "monthly",
        "package_fee": "1000.00",
        "fee": "1000.00",
        "override": None,
    }
    assert fees["can_set_fee"] is False

    asked = _ok(
        sita.post(
            f"/api/clients/{cid}/fee-overrides",
            json={"amount": "800.50", "reason": "Long-standing client, agreed rate"},
            headers=sita_s.headers,
        ),
        201,
    )
    assert asked["plan"]["fee"] == "1000.00"  # not until approved
    assert asked["pending"]["amount"] == "800.50"
    again = sita.post(
        f"/api/clients/{cid}/fee-overrides",
        json={"amount": "700", "reason": "Another try"},
        headers=sita_s.headers,
    )
    assert (again.status_code, again.json()["error"]["code"]) == (409, "override_pending")
    # Staff cannot decide.
    oid = asked["pending"]["id"]
    assert sita.get("/api/admin/fee-overrides").status_code == 403
    assert (
        sita.post(
            f"/api/admin/fee-overrides/{oid}/decision",
            json={"approve": True},
            headers=sita_s.headers,
        ).status_code
        == 403
    )

    waiting = _ok(web.get("/api/admin/fee-overrides"))
    assert [(o["client_id"], o["original"], o["amount"]) for o in waiting] == [
        (cid, "1000.00", "800.50")
    ]
    decided = _ok(
        web.post(
            f"/api/admin/fee-overrides/{oid}/decision",
            json={"approve": True},
            headers=admin.headers,
        )
    )
    assert (decided["status"], decided["requested_by"], decided["decided_by"]) == (
        "approved",
        "Sita Staff",
        "Test Person",
    )
    twice = web.post(
        f"/api/admin/fee-overrides/{oid}/decision",
        json={"approve": False, "note": "No"},
        headers=admin.headers,
    )
    assert (twice.status_code, twice.json()["error"]["code"]) == (409, "already_decided")

    # Overrides show the original and the approved amount, and who approved.
    fees = _ok(sita.get(f"/api/clients/{cid}/fees"))
    assert fees["plan"]["fee"] == "800.50"
    override = fees["plan"]["override"]
    assert (override["original"], override["amount"], override["decided_by"]) == (
        "1000.00",
        "800.50",
        "Test Person",
    )
    assert "fee.override_requested" in events(migrated_db)
    assert "fee.override_approved" in events(migrated_db)

    # Invoices use it.
    run(factory, make_settings(), now=at(today_ist()))
    fees = _ok(sita.get(f"/api/clients/{cid}/fees"))
    assert [(i["amount"], i["cgst"], i["sgst"], i["total"]) for i in fees["invoices"]] == [
        ("800.50", "72.05", "72.05", "944.60")  # 72.045 rounds half up
    ]

    # Another staff member sees nothing of it (FR-FE-05).
    oid2 = make_user(migrated_db, "ravi@firm.in", Role.STAFF, name="Ravi Staff")
    ravi = TestClient(web.app)
    sign_in_directly(migrated_db, ravi, oid2)
    assert ravi.get(f"/api/clients/{cid}/fees").status_code == 403


@pytestmark_db
def test_an_administrators_fee_applies_at_once_and_rejection_needs_a_reason(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    sid = make_user(migrated_db, "sita@firm.in", Role.STAFF, name="Sita Staff")
    sita = TestClient(web.app)
    sita_s = sign_in_directly(migrated_db, sita, sid)
    cid = new_client(web, admin, pid, 1, owner_id=sid)
    break_glass(web, admin, cid)  # not the Administrator's own client (C29)
    same = web.post(
        f"/api/clients/{cid}/fee-overrides",
        json={"amount": "1000", "reason": "No change at all"},
        headers=admin.headers,
    )
    assert (same.status_code, same.json()["error"]["code"]) == (409, "same_fee")
    set_now = _ok(
        web.post(
            f"/api/clients/{cid}/fee-overrides",
            json={"amount": "1200", "reason": "Extra filings agreed"},
            headers=admin.headers,
        ),
        201,
    )
    assert set_now["plan"]["fee"] == "1200.00"
    assert set_now["pending"] is None

    oid = _ok(
        sita.post(
            f"/api/clients/{cid}/fee-overrides",
            json={"amount": "900", "reason": "Client asked for less"},
            headers=sita_s.headers,
        ),
        201,
    )["pending"]["id"]
    bare = web.post(
        f"/api/admin/fee-overrides/{oid}/decision", json={"approve": False}, headers=admin.headers
    )
    assert (bare.status_code, bare.json()["error"]["code"]) == (409, "note_required")
    no = _ok(
        web.post(
            f"/api/admin/fee-overrides/{oid}/decision",
            json={"approve": False, "note": "Rate already discounted"},
            headers=admin.headers,
        )
    )
    assert (no["status"], no["decision_note"]) == ("rejected", "Rate already discounted")
    fees = _ok(sita.get(f"/api/clients/{cid}/fees"))
    assert fees["plan"]["fee"] == "1200.00"
    assert [o["status"] for o in fees["overrides"]] == ["rejected", "approved"]
    with db_session(migrated_db) as db, pytest.raises(DBAPIError, match="decided once"):
        db.execute(text("UPDATE fee_overrides SET status = 'approved' WHERE id = :i"), {"i": oid})
    with db_session(migrated_db) as db, pytest.raises(DBAPIError, match="never deleted"):
        db.execute(text("DELETE FROM fee_overrides"))


@pytestmark_db
def test_an_administrator_adds_a_tax_rate_from_a_later_date(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    rates = _ok(web.get("/api/admin/tax-rates"))
    assert [(r["rate"], r["effective_from"], r["in_force"]) for r in rates] == [
        ("18.00", "2017-07-01", True)
    ]
    today = today_ist()
    past = web.post(
        "/api/admin/tax-rates",
        json={"rate": "12", "effective_from": today.isoformat()},
        headers=admin.headers,
    )
    assert (past.status_code, past.json()["error"]["code"]) == (422, "date_in_past")
    start = today + timedelta(days=40)
    added = _ok(
        web.post(
            "/api/admin/tax-rates",
            json={"rate": "12.5", "effective_from": start.isoformat(), "note": "Budget change"},
            headers=admin.headers,
        ),
        201,
    )
    assert [(r["rate"], r["in_force"]) for r in added] == [("12.50", False), ("18.00", True)]
    dup = web.post(
        "/api/admin/tax-rates",
        json={"rate": "10", "effective_from": start.isoformat()},
        headers=admin.headers,
    )
    assert dup.status_code == 409
    assert "tax_rate.added" in events(migrated_db)

    # An invoice raised on or after the date uses the new rate; earlier ones keep 18%.
    pid = publish_package(web, admin)
    new_client(web, admin, pid, 1)
    run(factory, make_settings(), now=at(today))
    run(factory, make_settings(), now=at(start + timedelta(days=31)))
    rates_used = [r.tax_rate_bp for r in invoices(migrated_db)]
    assert rates_used[0] == 1800
    assert rates_used[-1] == 1250
