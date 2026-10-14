"""Chunk C12: bulk due-date extension (BRD FR-PK-06)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from app.models import Cycle, DueDateChange, DueDateExtension, HolidayRule, Stage
from app.schedule.engine import apply_holiday_rule
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from tests.audit_helpers import entries
from tests.helpers import Signed, db_session
from tests.test_clients import _ok, admin, client_body, create, publish_package

__all__ = ["admin"]  # the fixture, re-used from test_clients

pytestmark = pytest.mark.requires_db

LEAD = 4  # the seeded monthly default


def filing_of(url: str) -> int:
    with db_session(url) as db:
        return int(db.scalars(select(Cycle.filing_id)).first() or 0)


def first_period(web: TestClient, filing_id: int) -> dict[str, Any]:
    found = _ok(web.get("/api/admin/extensions/periods", params={"filing_id": filing_id}))
    assert found, "the generator made no cycles"
    return dict(found[0])


def cycles_for(url: str, period_start: str) -> dict[int, Cycle]:
    with db_session(url) as db:
        rows = db.scalars(
            select(Cycle).where(Cycle.period_start == date.fromisoformat(period_start))
        )
        return {c.client_pk: c for c in rows}


def new_client(web: TestClient, admin: Signed, pid: int, pan: str) -> int:
    body = client_body(pid, owner_id=admin.user_id, pan=pan)
    return int(_ok(create(web, admin, body), 201)["client_id"])


def body(filing_id: int, period: dict[str, Any], new_due: date, **over: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "filing_id": filing_id,
        "period_start": period["period_start"],
        "new_due_date": new_due.isoformat(),
    }
    out.update(over)
    return out


def expected_alert(due: date, holidays: set[date] | None = None) -> date:
    return apply_holiday_rule(
        due - timedelta(days=LEAD), HolidayRule.EARLIER_WORKING_DAY, holidays or set()
    )


def set_stage(url: str, cycle_id: int, stage: Stage) -> None:
    with db_session(url) as db:
        db.execute(
            text("UPDATE cycles SET stage = :s WHERE id = :i"), {"s": stage.value, "i": cycle_id}
        )
        db.commit()


# --- Done when: affected cycles show the new date, alerts are recomputed, change is logged ---


def test_an_extension_moves_open_cycles_and_recomputes_their_alerts(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    for pan in ("ABCPA1234F", "ABCPB1234F", "ABCPC1234F"):
        new_client(web, admin, pid, pan)
    filing_id = filing_of(migrated_db)
    period = first_period(web, filing_id)
    assert period["open_cycles"] == 3
    before = cycles_for(migrated_db, period["period_start"])
    filed, *open_ = sorted(before.values(), key=lambda c: c.id)
    set_stage(migrated_db, filed.id, Stage.FILED)  # done before the extension: keeps its date

    new_due = date.fromisoformat(period["earliest_due"]) + timedelta(days=10)
    # A holiday on the plain alert date moves it to the working day before.
    plain = new_due - timedelta(days=LEAD)
    _ok(
        web.post(
            "/api/admin/holidays",
            json={"day": plain.isoformat(), "name": "Test holiday"},
            headers=admin.headers,
        ),
        201,
    )
    alert = expected_alert(new_due, {plain})
    assert alert < plain

    shown = _ok(
        web.post(
            "/api/admin/extensions/preview",
            json=body(filing_id, period, new_due),
            headers=admin.headers,
        )
    )
    assert (shown["changes"], shown["unchanged"], shown["period_label"]) == (
        2,
        0,
        period["period_label"],
    )
    row = shown["rows"][0]
    assert row["code"].startswith("TKT-")
    assert (row["old_due_date"], row["new_due_date"], row["new_alert_date"]) == (
        open_[0].due_date.isoformat(),
        new_due.isoformat(),
        alert.isoformat(),
    )
    # A preview changes nothing.
    assert cycles_for(migrated_db, period["period_start"])[open_[0].client_pk].due_date == (
        open_[0].due_date
    )

    applied = _ok(
        web.post(
            "/api/admin/extensions",
            json=body(
                filing_id,
                period,
                new_due,
                reason="  CBIC notification 12/2026: GSTR-1 extended ",
                expected_changes=2,
            ),
            headers=admin.headers,
        ),
        201,
    )
    assert applied["cycles_changed"] == 2
    assert applied["reason"] == "CBIC notification 12/2026: GSTR-1 extended"

    after = cycles_for(migrated_db, period["period_start"])
    for c in open_:
        assert (after[c.client_pk].due_date, after[c.client_pk].alert_date) == (new_due, alert)
    assert (after[filed.client_pk].due_date, after[filed.client_pk].alert_date) == (
        filed.due_date,
        filed.alert_date,
    )

    # Logged: one audit entry, one history row per cycle, shown on the ticket.
    logged = [e for e in entries(migrated_db) if e.event == "calendar.due_date_extended"]
    assert len(logged) == 1
    assert logged[0].details["cycles_changed"] == 2
    assert logged[0].details["new_due_date"] == new_due.isoformat()
    with db_session(migrated_db) as db:
        changes = list(db.scalars(select(DueDateChange)))
    assert {c.cycle_id for c in changes} == {c.id for c in open_}
    ticket = _ok(web.get(f"/api/tickets/{row['code']}"))
    assert ticket["cycle"]["due_date"] == new_due.isoformat()
    ext = ticket["cycle"]["extensions"][0]
    assert (ext["old_due_date"], ext["new_due_date"], ext["reason"]) == (
        open_[0].due_date.isoformat(),
        new_due.isoformat(),
        "CBIC notification 12/2026: GSTR-1 extended",
    )
    listed = _ok(web.get("/api/admin/extensions"))
    assert [(e["period_label"], e["cycles_changed"]) for e in listed] == [
        (period["period_label"], 2)
    ]

    # Applying the same extension again has nothing to move.
    again = web.post(
        "/api/admin/extensions",
        json=body(filing_id, period, new_due, reason="Again", expected_changes=0),
        headers=admin.headers,
    )
    assert again.json()["error"]["code"] == "nothing_to_change"


def test_a_client_added_after_an_extension_gets_the_extended_date(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid, "ABCPA1234F")
    filing_id = filing_of(migrated_db)
    period = first_period(web, filing_id)
    new_due = date.fromisoformat(period["earliest_due"]) + timedelta(days=7)
    _ok(
        web.post(
            "/api/admin/extensions",
            json=body(filing_id, period, new_due, reason="Portal outage", expected_changes=1),
            headers=admin.headers,
        ),
        201,
    )
    later = new_client(web, admin, pid, "ABCPB1234F")
    with db_session(migrated_db) as db:
        pk = db.scalar(text("SELECT id FROM clients WHERE client_id = :c"), {"c": later})
        cycle = db.scalar(
            select(Cycle).where(
                Cycle.client_pk == pk,
                Cycle.period_start == date.fromisoformat(period["period_start"]),
            )
        )
        assert cycle is not None
        assert (cycle.due_date, cycle.alert_date) == (new_due, expected_alert(new_due))
        change = db.scalar(select(DueDateChange).where(DueDateChange.cycle_id == cycle.id))
        assert change is not None
        assert change.old_due_date == date.fromisoformat(period["earliest_due"])


# --- Refusals ----------------------------------------------------------------------------------


def test_a_stale_preview_changes_nothing(web: TestClient, admin: Signed, migrated_db: str) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid, "ABCPA1234F")
    new_client(web, admin, pid, "ABCPB1234F")
    filing_id = filing_of(migrated_db)
    period = first_period(web, filing_id)
    new_due = date.fromisoformat(period["earliest_due"]) + timedelta(days=5)
    before = cycles_for(migrated_db, period["period_start"])
    # One is filed between the preview (2 cycles) and the apply.
    set_stage(migrated_db, min(c.id for c in before.values()), Stage.FILED)
    stale = web.post(
        "/api/admin/extensions",
        json=body(filing_id, period, new_due, reason="Extension", expected_changes=2),
        headers=admin.headers,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "preview_stale"
    after = cycles_for(migrated_db, period["period_start"])
    assert {pk: c.due_date for pk, c in after.items()} == {
        pk: c.due_date for pk, c in before.items()
    }
    with db_session(migrated_db) as db:
        assert db.scalar(select(DueDateExtension.id)) is None
    assert "calendar.due_date_extended" not in {e.event for e in entries(migrated_db)}


def test_what_an_extension_needs(web: TestClient, admin: Signed, migrated_db: str) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid, "ABCPA1234F")
    filing_id = filing_of(migrated_db)
    period = first_period(web, filing_id)
    start = date.fromisoformat(period["period_start"])

    def preview(**over: Any) -> Any:
        return web.post(
            "/api/admin/extensions/preview",
            json={**body(filing_id, period, start + timedelta(days=60)), **over},
            headers=admin.headers,
        )

    assert preview(new_due_date=start.isoformat()).json()["error"]["code"] == "bad_date"
    assert preview(new_due_date="2099-01-01").json()["error"]["code"] == "bad_date"
    assert preview(period_start="1999-04-01").json()["error"]["code"] == "no_cycles"
    assert preview(filing_id=999999).status_code == 404
    # Not later than the current date: nothing changes, and the preview says so.
    early = preview(new_due_date=(date.fromisoformat(period["earliest_due"])).isoformat())
    assert (_ok(early)["changes"], _ok(early)["unchanged"]) == (0, 1)
    no_reason = web.post(
        "/api/admin/extensions",
        json=body(filing_id, period, start + timedelta(days=60), reason=" ", expected_changes=1),
        headers=admin.headers,
    )
    assert no_reason.status_code == 422
    assert web.get("/api/admin/extensions/periods", params={"filing_id": 999999}).status_code == 404


def test_extension_records_cannot_be_changed(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid, "ABCPA1234F")
    filing_id = filing_of(migrated_db)
    period = first_period(web, filing_id)
    new_due = date.fromisoformat(period["earliest_due"]) + timedelta(days=3)
    _ok(
        web.post(
            "/api/admin/extensions",
            json=body(filing_id, period, new_due, reason="Extension", expected_changes=1),
            headers=admin.headers,
        ),
        201,
    )
    engine = create_engine(migrated_db)
    try:
        for stmt in (
            "UPDATE due_date_extensions SET reason = 'x'",
            "DELETE FROM due_date_extensions",
            "UPDATE due_date_changes SET new_due_date = old_due_date",
            "DELETE FROM due_date_changes",
        ):
            with pytest.raises(Exception, match="append-only"), engine.begin() as conn:
                conn.execute(text(stmt))
    finally:
        engine.dispose()
