"""Chunk C28: reports and exports (BRD FR-RP-03, FR-RP-04, FR-AU-01)."""

from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.reports.files import pdf, xlsx
from app.timeutil import today_ist
from fastapi.testclient import TestClient
from sqlalchemy import text

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session, sign_in_directly
from tests.test_assignments import setup
from tests.test_clients import _ok, admin
from tests.test_dashboard import factory, portfolio
from tests.test_documents import PDF, scanner
from tests.test_filing import ready_to_file
from tests.test_tickets import checker

__all__ = ["admin", "checker", "factory", "portfolio", "scanner"]  # re-used fixtures

REPORTS = ("filing-status", "late-filings", "fee-aging", "consultant-turnaround", "alert-delivery")


# --- The files ------------------------------------------------------------------------------


def sheet_xml(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        assert "[Content_Types].xml" in zf.namelist()
        return zf.read("xl/worksheets/sheet1.xml").decode()


def test_the_spreadsheet_keeps_text_as_text() -> None:
    data = xlsx(
        "Fee aging",
        ["Client", "Owed", "Since"],
        [
            ['=HYPERLINK("http://evil","x")', Decimal("1180.00"), date(2026, 5, 1)],
            ["A & B <c>", 7, None],
        ],
    )
    sheet = sheet_xml(data)
    assert "<f>" not in sheet  # no formulas, ever
    assert "=HYPERLINK(&quot;http://evil&quot;,&quot;x&quot;)" in sheet
    assert "A &amp; B &lt;c&gt;" in sheet
    assert '<c r="B2"><v>1180.00</v></c>' in sheet  # numbers stay numbers
    assert "2026-05-01" in sheet
    assert xlsx("Fee aging", ["A"], [["x"]]) == xlsx("Fee aging", ["A"], [["x"]])  # stable bytes


def test_the_pdf_is_a_paged_table() -> None:
    rows: list[list[Any]] = [[f"Client {n}", "₹ 1,180", n] for n in range(120)]
    data = pdf("Fee aging", "all owners", ["Client", "Owed", "N"], rows)
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    assert data.count(b"/Type /Page ") == 3  # 120 rows: three pages
    assert b"Rs. 1,180" in data  # the rupee sign, in a font that has no rupee glyph
    assert b"(Client 119)" in data
    tricky = pdf("T", "s", ["A"], [["a (b) \\ c"]])
    assert b"a \\(b\\) \\\\ c" in tricky


# --- LL-116, LL-117: the reports ---------------------------------------------------------------


def test_each_report_runs_for_the_administrator(
    web: TestClient, admin: Signed, portfolio: dict[str, Any]
) -> None:
    for name in REPORTS:
        report = _ok(web.get(f"/api/reports/{name}"))
        assert report["report"] == name
        assert report["total"] == len(report["rows"])
        assert all(len(r) == len(report["columns"]) for r in report["rows"])

    status = _ok(web.get("/api/reports/filing-status"))
    assert status["columns"][:4] == ["Filing", "Period", "Due", "Cycles"]
    for row in status["rows"]:
        assert row[3] == sum(row[4:])  # every cycle is in exactly one column

    aging = _ok(web.get("/api/reports/fee-aging"))
    totals = {r[0]: r for r in aging["rows"]}
    for row in totals.values():
        assert sum(Decimal(v) for v in row[3:8]) == Decimal(row[8])
    ravi = next(r for r in aging["rows"] if r[2] == "Ravi Staff")
    assert Decimal(ravi[8]) == Decimal("2360.00")  # last month's (overdue) and this month's
    assert ravi[3] == "1180.00"  # this month's, not yet due

    turnaround = _ok(web.get("/api/reports/consultant-turnaround"))
    assert turnaround["rows"] == [["Ravi Consultant", "Consultant", 1, 0, None, None, None, 1]]


def test_late_filings_need_a_reason_and_the_report_shows_it(
    web: TestClient,
    admin: Signed,
    checker: tuple[TestClient, Signed],
    migrated_db: str,
    scanner: object,
) -> None:
    _, code, _ = setup(web, admin, migrated_db)
    ready_to_file(web, admin, checker, code)
    due = today_ist() - timedelta(days=4)
    with db_session(migrated_db) as db:
        db.execute(
            text(
                "UPDATE cycles c JOIN tickets t ON t.id = c.ticket_id SET c.due_date = :d,"
                " c.alert_date = :d WHERE t.number = :n"
            ),
            {"d": due, "n": int(code.removeprefix("TKT-"))},
        )
        db.commit()

    def file(reason: str | None) -> Any:
        params = {"reference": "AA0705260012345", **({"late_reason": reason} if reason else {})}
        return web.post(
            f"/api/uploads/tickets/{code}/filing",
            params=params,
            content=PDF,
            headers={**admin.headers, "X-File-Name": "ack.pdf"},
        )

    for missing in (None, "late"):
        refused = file(missing)
        assert (refused.status_code, refused.json()["error"]["code"]) == (
            422,
            "late_reason_required",
        )
    _ok(file("Client sent the sales register on the due date"))

    late = _ok(web.get("/api/reports/late-filings"))
    row = next(r for r in late["rows"] if r[0] == code)
    columns = late["columns"]
    assert row[columns.index("Status")] == "Filed late"
    assert row[columns.index("Days late")] == 4
    assert row[columns.index("Reason")] == "Client sent the sales register on the due date"
    status = _ok(web.get("/api/reports/filing-status"))
    assert sum(r[status["columns"].index("Filed late")] for r in status["rows"]) == 1


# --- LL-118, LL-119: exports follow the screens' access rules and are audited -----------------


def test_a_staff_export_holds_only_their_rows_and_is_logged(
    web: TestClient, admin: Signed, migrated_db: str, portfolio: dict[str, Any]
) -> None:
    sita_id, sita = portfolio["sita"]
    ravi_id, _ = portfolio["ravi"]
    sita_s = sign_in_directly(migrated_db, sita, sita_id)
    mine = _ok(sita.get("/api/reports/fee-aging"))
    assert mine["owner_id"] == sita_id
    assert {r[2] for r in mine["rows"]} == {"Sita Staff"}
    assert sita.get("/api/reports/fee-aging", params={"owner_id": ravi_id}).status_code == 403

    got = sita.post(
        "/api/reports/fee-aging/export", json={"format": "xlsx"}, headers=sita_s.headers
    )
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert got.headers["content-disposition"].startswith(
        'attachment; filename="ledgerline-fee-aging-'
    )
    assert got.headers["cache-control"] == "no-store"
    sheet = sheet_xml(got.content)
    assert "Sita Staff" in sheet
    assert "Ravi Staff" not in sheet

    as_pdf = sita.post(
        "/api/reports/late-filings/export", json={"format": "pdf"}, headers=sita_s.headers
    )
    assert as_pdf.content.startswith(b"%PDF")
    assert (
        sita.post(
            "/api/reports/fee-aging/export",
            json={"format": "xlsx", "owner_id": ravi_id},
            headers=sita_s.headers,
        ).status_code
        == 403
    )
    # Without the CSRF token, refused.
    assert sita.post("/api/reports/fee-aging/export", json={"format": "xlsx"}).status_code == 403

    logged = [e for e in entries(migrated_db) if e.event == "report.exported"]
    assert [
        (e.actor_user_id, e.target_id, e.details["format"], e.details["owner_id"]) for e in logged
    ] == [
        (sita_id, "fee-aging", "xlsx", sita_id),
        (sita_id, "late-filings", "pdf", sita_id),
    ]
    assert logged[0].details["rows"] == len(mine["rows"])
    # Looking at a report on screen is not an export.
    assert len(logged) == 2


def test_bad_report_requests_are_refused(web: TestClient, admin: Signed) -> None:
    today = today_ist()
    for params in (
        {"from": today.isoformat(), "to": (today - timedelta(days=1)).isoformat()},
        {"from": (today - timedelta(days=800)).isoformat(), "to": today.isoformat()},
    ):
        bad = web.get("/api/reports/filing-status", params=params)
        assert (bad.status_code, bad.json()["error"]["code"]) == (422, "bad_range")
    assert web.get("/api/reports/everything").status_code == 422
    assert (
        web.post(
            "/api/reports/fee-aging/export", json={"format": "csv"}, headers=admin.headers
        ).status_code
        == 422
    )
    # The work list's count report still answers at its own address.
    assert web.get("/api/reports/worklist-counts").status_code == 200
