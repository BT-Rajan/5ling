"""Chunk C27: dashboards (BRD FR-RP-01, FR-RP-02)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.fees.job import run
from app.models import Role
from app.timeutil import today_ist
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import make_settings
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_assignments import assign, consultant
from tests.test_clients import _ok, admin, publish_package
from tests.test_fees import at, factory, new_client
from tests.test_payments import pay

__all__ = ["admin", "factory"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db


def figures(dash: dict[str, Any]) -> list[tuple[str, str | None, int]]:
    """Every figure on a dashboard, as (figure, key, count)."""
    out: list[tuple[str, str | None, int]] = [
        ("stage", s["stage"], s["count"]) for s in dash["stages"]
    ]
    out += [("overdue_filings", None, dash["overdue_filings"])]
    out += [("documents_missing", None, dash["documents_missing"])]
    out += [("workload", str(p["id"]), p["count"]) for p in dash["workload"]]
    out += [("fees_due", None, dash["fees_due"]["count"])]
    out += [("fees_overdue", None, dash["fees_overdue"]["count"])]
    return out


def listed(browser: TestClient, figure: str, key: str | None, **params: Any) -> Any:
    query = {"figure": figure, **({"key": key} if key else {}), **params}
    return _ok(browser.get("/api/dashboard/list", params=query))


def staff_user(web: TestClient, url: str, email: str, name: str) -> tuple[int, TestClient]:
    uid = make_user(url, email, Role.STAFF, name=name)
    browser = TestClient(web.app)
    sign_in_directly(url, browser, uid)
    return uid, browser


def first_code(web: TestClient, cid: int) -> str:
    return str(
        _ok(web.get("/api/tickets", params={"client_id": cid, "limit": 1}))["items"][0]["code"]
    )


@pytest.fixture
def portfolio(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> dict[str, Any]:
    """Two owners' clients, with work in several stages, an assignment, and fees due and overdue."""
    sita_id, sita = staff_user(web, migrated_db, "sita@firm.in", "Sita Staff")
    ravi_id, ravi = staff_user(web, migrated_db, "ravi@firm.in", "Ravi Staff")
    pid = publish_package(web, admin)
    sita_client = new_client(web, admin, pid, 1, owner_id=sita_id)
    ravi_client = new_client(web, admin, pid, 2, owner_id=ravi_id)
    sita_code, ravi_code = first_code(web, sita_client), first_code(web, ravi_client)
    today = today_ist()
    with db_session(migrated_db) as db:
        # Sita's first filing is overdue; Ravi's is due in five days.
        for code, due in (
            (sita_code, today - timedelta(days=3)),
            (ravi_code, today + timedelta(days=5)),
        ):
            db.execute(
                text(
                    "UPDATE cycles c JOIN tickets t ON t.id = c.ticket_id SET c.due_date = :d,"
                    " c.alert_date = :d WHERE t.number = :n"
                ),
                {"d": due, "n": int(code.removeprefix("TKT-"))},
            )
        # Ravi's client joined on the 1st of last month, so it has an overdue invoice too.
        start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        db.execute(
            text("UPDATE clients SET created_at = :d WHERE client_id = :c"),
            {"d": f"{start} 03:00:00", "c": ravi_client},
        )
        db.commit()

    def move(code: str, action: str, at_: str) -> None:
        _ok(
            web.post(
                f"/api/tickets/{code}/transition",
                json={"action": action, "expected_stage": at_},
                headers=admin.headers,
            )
        )

    move(sita_code, "request_documents", "scheduled")
    move(ravi_code, "request_documents", "scheduled")
    move(ravi_code, "documents_received", "awaiting_documents")
    cons_id, _, _ = consultant(web, migrated_db)
    _ok(assign(web, admin, ravi_code, cons_id))
    run(factory, make_settings(invoice_terms_days=10), now=at(start))
    run(factory, make_settings(invoice_terms_days=10), now=at(today))
    inv = _ok(web.get(f"/api/clients/{sita_client}/fees"))["invoices"][0]["id"]
    _ok(pay(web, admin, inv, "100"), 201)
    return {
        "sita": (sita_id, sita),
        "ravi": (ravi_id, ravi),
        "consultant": cons_id,
        "clients": {sita_client: sita_id, ravi_client: ravi_id},
        "codes": {"sita": sita_code, "ravi": ravi_code},
    }


# --- LL-113, LL-115: each figure opens its list; the list size equals the figure ---------------


def test_every_administrator_figure_equals_its_list(
    web: TestClient, admin: Signed, portfolio: dict[str, Any]
) -> None:
    dash = _ok(web.get("/api/dashboard"))
    stages = {s["stage"]: s["count"] for s in dash["stages"]}
    assert "closed" not in stages
    assert stages["awaiting_documents"] == 1
    assert stages["assigned"] == 1
    assert dash["fees_overdue"] == {"count": 1, "amount": "1180.00"}
    assert dash["fees_due"] == {"count": 2, "amount": "2260.00"}  # 1,180 + (1,180 - 100)
    workload = {p["id"]: (p["full_name"], p["count"]) for p in dash["workload"]}
    assert workload[portfolio["consultant"]] == ("Ravi Consultant", 1)  # assigned: the assignee
    assert portfolio["sita"][0] in workload  # overdue, unassigned: the owner's

    for figure, key, count in figures(dash):
        page = listed(web, figure, key)
        assert page["total"] == count, (figure, key)
        assert len(page["cycles"]) + len(page["invoices"]) == count, (figure, key)
    overdue = [c["code"] for c in listed(web, "overdue_filings", None)["cycles"]]
    assert portfolio["codes"]["sita"] in overdue
    assert portfolio["codes"]["ravi"] not in overdue  # due in five days
    assigned = listed(web, "workload", str(portfolio["consultant"]))["cycles"]
    assert [c["code"] for c in assigned] == [portfolio["codes"]["ravi"]]
    late = listed(web, "fees_overdue", None)["invoices"]
    assert [(i["balance"], i["total"]) for i in late] == [("1180.00", "1180.00")]


# --- LL-114, LL-115: Staff see only their own clients in every figure --------------------------


def test_staff_figures_never_include_other_owners(
    web: TestClient, admin: Signed, portfolio: dict[str, Any]
) -> None:
    sita_id, sita = portfolio["sita"]
    ravi_id, _ = portfolio["ravi"]
    mine = {cid for cid, owner in portfolio["clients"].items() if owner == sita_id}
    dash = _ok(sita.get("/api/dashboard"))
    assert dash["owner_id"] == sita_id
    # The same as the Administrator's view of Sita's clients, and less than everyone's.
    assert dash == _ok(web.get("/api/dashboard", params={"owner_id": sita_id}))
    everyone = _ok(web.get("/api/dashboard"))
    assert sum(s["count"] for s in dash["stages"]) < sum(s["count"] for s in everyone["stages"])
    assert dash["fees_overdue"]["count"] == 0  # the overdue invoice is Ravi's client's
    assert portfolio["consultant"] not in [p["id"] for p in dash["workload"]]

    for figure, key, count in figures(dash):
        page = listed(sita, figure, key)
        assert page["total"] == count, (figure, key)
        seen = {c["client_id"] for c in page["cycles"]} | {i["client_id"] for i in page["invoices"]}
        assert seen <= mine, (figure, key)
    # Asking for someone else's is refused, for the dashboard and every list.
    assert sita.get("/api/dashboard", params={"owner_id": ravi_id}).status_code == 403
    assert (
        sita.get(
            "/api/dashboard/list", params={"figure": "fees_overdue", "owner_id": ravi_id}
        ).status_code
        == 403
    )
    # Ravi's consultant's workload, asked for by Sita, is empty: it is not her client.
    assert listed(sita, "workload", str(portfolio["consultant"]))["total"] == 0


def test_a_bad_figure_is_refused(web: TestClient, admin: Signed, portfolio: dict[str, Any]) -> None:
    for params in (
        {"figure": "stage", "key": "closed"},
        {"figure": "stage", "key": "nonsense"},
        {"figure": "workload"},
        {"figure": "workload", "key": "abc"},
    ):
        assert web.get("/api/dashboard/list", params=params).status_code == 422, params
    assert web.get("/api/dashboard/list", params={"figure": "everything"}).status_code == 422
