"""Chunk C20: the daily work list (BRD FR-IN-04)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from app.alerts.job import run
from app.db import make_engine, make_session_factory
from app.files.checklist import items_for
from app.models import AlertKind, Client, ClientStatus, Cycle, CycleAlert, Role, Stage
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.helpers import (
    Signed,
    break_glass,
    db_session,
    give_filing_proof,
    make_user,
    sign_in_directly,
)
from tests.test_alerts import at
from tests.test_checklist import CHECKLIST, publish_with_checklist
from tests.test_clients import _ok, admin, client_body, create
from tests.test_documents import PDF, FakeScanner, scanner

__all__ = ["admin", "scanner"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db

FILED = {Stage.FILED, Stage.ACKNOWLEDGED, Stage.CLOSED}
GRACE = 3


@pytest.fixture
def factory(migrated_db: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(migrated_db)
    yield make_session_factory(engine)
    engine.dispose()


def expected(url: str, on: date, owner_id: int | None) -> dict[str, set[int]]:
    """The four lists, worked out here from the rows themselves, not with the app's queries."""
    monday = on - timedelta(days=on.weekday())
    sunday = monday + timedelta(days=6)
    out: dict[str, set[int]] = {
        b: set() for b in ("received", "missing", "due_this_week", "overdue")
    }
    with db_session(url) as db:
        firsts = {
            a.cycle_id: a.sent_on
            for a in db.scalars(select(CycleAlert).where(CycleAlert.kind == AlertKind.FIRST))
        }
        for cycle, client in db.execute(
            select(Cycle, Client).join(Client, Client.id == Cycle.client_pk)
        ):
            if client.status is ClientStatus.CLOSED or (owner_id and client.owner_id != owner_id):
                continue
            number = db.scalar(
                text("SELECT number FROM tickets WHERE id = :i"), {"i": cycle.ticket_id}
            )
            if cycle.stage is Stage.DOCUMENTS_RECEIVED:
                out["received"].add(number)
            first = firsts.get(cycle.id)
            views = items_for(db, cycle.id)
            if (
                cycle.stage in (Stage.SCHEDULED, Stage.AWAITING_DOCUMENTS)
                and first is not None
                and first <= on - timedelta(days=GRACE)
                and any(v.item.required and v.status != "received" for v in views)
            ):
                out["missing"].add(number)
            if cycle.stage not in FILED and monday <= cycle.due_date <= sunday:
                out["due_this_week"].add(number)
            if cycle.stage not in FILED and cycle.due_date < on:
                out["overdue"].add(number)
    return out


def numbers(listing: dict[str, Any]) -> set[int]:
    return {int(r["code"].split("-")[1]) for r in listing["items"]}


def check(web: TestClient, url: str, on: date, owner_id: int | None) -> dict[str, Any]:
    params: dict[str, Any] = {"on": on.isoformat()}
    if owner_id is not None:
        params["owner_id"] = owner_id
    listed: dict[str, Any] = _ok(web.get("/api/worklist", params=params))
    report = _ok(web.get("/api/reports/worklist-counts", params=params))
    want = expected(url, on, owner_id)
    for bucket, cycles in want.items():
        # FR-IN-04: the work list, the report and the rows themselves all agree.
        assert listed["counts"][bucket] == report["counts"][bucket] == len(cycles), (bucket, on)
        assert listed["lists"][bucket]["total"] == len(cycles)
        assert numbers(listed["lists"][bucket]) == cycles, (bucket, on)
    return listed


# --- Done when: work-list counts equal report counts for the same owner and date -------------


def test_work_list_counts_equal_report_counts_for_each_owner_and_date(
    web: TestClient,
    admin: Signed,
    migrated_db: str,
    scanner: FakeScanner,
    factory: sessionmaker[Session],
) -> None:
    pid, _ = publish_with_checklist(web, admin, CHECKLIST)
    staff_id = make_user(migrated_db, "staff@firm.in", Role.STAFF, name="Staff Person")
    mine = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    _ok(create(web, admin, client_body(pid, owner_id=staff_id, pan="ABCPF1234G")), 201)
    closed = _ok(create(web, admin, client_body(pid, owner_id=staff_id, pan="ABCPG1234H")), 201)[
        "client_id"
    ]
    break_glass(web, admin, closed)  # a Staff client (C29)
    _ok(
        web.patch(
            f"/api/clients/{closed}",
            json={"status": "closed", "reason": "Left"},
            headers=admin.headers,
        )
    )

    with db_session(migrated_db) as db:
        cycles = list(db.scalars(select(Cycle).order_by(Cycle.period_start, Cycle.id)))
    first = cycles[0]
    settings = web.app.state.settings  # type: ignore[attr-defined]
    run(factory, settings, now=at(first.alert_date))  # first alerts for every early cycle
    # One cycle gets all its documents (Documents received); one is filed.
    mine_code = _ok(web.get("/api/tickets", params={"client_id": mine, "limit": 1}))["items"][0][
        "code"
    ]
    for item in _ok(web.get(f"/api/tickets/{mine_code}/checklist")):
        _ok(
            web.post(
                f"/api/uploads/tickets/{mine_code}",
                params={"item_id": item["id"]},
                content=PDF,
                headers={**admin.headers, "X-File-Name": "a.pdf"},
            ),
            201,
        )
    with db_session(migrated_db) as db:
        give_filing_proof(db, cycles[-1].id)
        db.execute(text("UPDATE cycles SET stage = 'filed' WHERE id = :i"), {"i": cycles[-1].id})
        db.commit()

    days = [
        first.alert_date,
        first.alert_date + timedelta(days=GRACE),
        first.due_date,
        first.due_date + timedelta(days=40),
    ]
    for on in days:
        for owner in (None, admin.user_id, staff_id):
            check(web, migrated_db, on, owner)
    later = check(web, migrated_db, first.due_date + timedelta(days=40), None)
    assert later["counts"]["received"] == 1
    assert later["counts"]["overdue"] > 0
    missing = later["lists"]["missing"]["items"]
    assert missing
    assert missing[0]["missing"] == ["Sales register", "Purchase register", "Notes for us"]


def test_staff_see_only_their_own_list(web: TestClient, admin: Signed, migrated_db: str) -> None:
    pid, _ = publish_with_checklist(web, admin, CHECKLIST)
    staff_id = make_user(migrated_db, "staff@firm.in", Role.STAFF, name="Staff Person")
    _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)
    staff = TestClient(web.app)
    sign_in_directly(migrated_db, staff, staff_id)
    own = _ok(staff.get("/api/worklist"))
    assert own["owner"] == {"id": staff_id, "full_name": "Staff Person"}
    assert own["counts"] == {"received": 0, "missing": 0, "due_this_week": 0, "overdue": 0}
    assert staff.get("/api/worklist", params={"owner_id": admin.user_id}).status_code == 403
    assert (
        staff.get("/api/reports/worklist-counts", params={"owner_id": admin.user_id}).status_code
        == 403
    )
    everyone = _ok(web.get("/api/worklist"))
    assert everyone["owner"] is None
    assert web.get("/api/worklist", params={"on": "1990-01-01"}).status_code == 422
    assert web.get("/api/worklist", params={"owner_id": 999999}).status_code == 404
