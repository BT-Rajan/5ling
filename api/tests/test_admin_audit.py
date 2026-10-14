"""Chunk C04: the Administrator reads, filters, checks and exports the audit log (FR-AU-04)."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest
from app.audit import append, make_record
from app.main import create_app
from app.models import Role, User
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.audit_helpers import events
from tests.conftest import make_settings
from tests.helpers import Signed, db_session, make_user, sign_in
from tests.routewalk import api_routes

pytestmark = pytest.mark.requires_db


def _admin(web: TestClient, url: str, **extra: object) -> Signed:
    make_user(url, "boss@firm.in", Role.ADMINISTRATOR, **extra)  # type: ignore[arg-type]
    return sign_in(web, "boss@firm.in")


def _record(url: str, event: str, *, at: datetime | None = None, **fields: object) -> None:
    with db_session(url) as db:
        record = make_record(event, **fields)  # type: ignore[arg-type]
        if at is not None:
            record = type(record)(**{**record.__dict__, "occurred_at": at})
        append(db, [record])
        db.commit()


def test_entries_are_listed_newest_first_and_page_without_overlap(
    web: TestClient, migrated_db: str
) -> None:
    _admin(web, migrated_db)
    for n in range(7):
        _record(migrated_db, "test.page", details={"n": n})
    first = web.get("/api/admin/audit/entries", params={"event": "test.page", "limit": 3}).json()
    assert [i["details"]["n"] for i in first["items"]] == [6, 5, 4]
    second = web.get(
        "/api/admin/audit/entries",
        params={"event": "test.page", "limit": 3, "before_seq": first["next_before_seq"]},
    ).json()
    third = web.get(
        "/api/admin/audit/entries",
        params={"event": "test.page", "limit": 3, "before_seq": second["next_before_seq"]},
    ).json()
    assert [i["details"]["n"] for i in second["items"]] == [3, 2, 1]
    assert [i["details"]["n"] for i in third["items"]] == [0]
    assert third["next_before_seq"] is None


def test_filters_by_person_record_and_india_dates(web: TestClient, migrated_db: str) -> None:
    admin = _admin(web, migrated_db, name="The Boss")
    # 18:29 UTC on 31 Oct is 23:59 on 31 Oct in India; 18:31 UTC is already 1 Nov there.
    _record(migrated_db, "test.when", at=datetime(2026, 10, 31, 18, 29, tzinfo=UTC))
    _record(migrated_db, "test.when", at=datetime(2026, 10, 31, 18, 31, tzinfo=UTC))
    _record(migrated_db, "test.who", actor_user_id=admin.user_id, target=("client", 100001))

    def seqs(**params: object) -> list[str]:
        resp = web.get("/api/admin/audit/entries", params=params)
        assert resp.status_code == 200, resp.text
        return [i["event"] for i in resp.json()["items"]]

    assert seqs(event="test.when", to_date="2026-10-31") == ["test.when"]
    assert seqs(event="test.when", from_date="2026-11-01", to_date="2026-11-01") == ["test.when"]
    assert seqs(event="test.when", from_date="2026-11-02") == []
    who = web.get(
        "/api/admin/audit/entries",
        params={"actor_user_id": admin.user_id, "target_type": "client", "target_id": "100001"},
    ).json()["items"]
    assert [(i["event"], i["actor_name"]) for i in who] == [("test.who", "The Boss")]
    bad = web.get(
        "/api/admin/audit/entries", params={"from_date": "2026-11-02", "to_date": "2026-11-01"}
    )
    assert bad.status_code == 422


def test_the_event_list_offers_every_kind_recorded(web: TestClient, migrated_db: str) -> None:
    _admin(web, migrated_db)
    _record(migrated_db, "test.b")
    _record(migrated_db, "test.a")
    listed = web.get("/api/admin/audit/events").json()
    assert {"test.a", "test.b", "login.succeeded"} <= set(listed)
    assert listed == sorted(listed)


def test_check_now_is_recorded_with_who_asked(web: TestClient, migrated_db: str) -> None:
    admin = _admin(web, migrated_db)
    assert web.get("/api/admin/audit/status").json() == {"latest": None}
    resp = web.post("/api/admin/audit/verify", headers=admin.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert (body["ok"], body["trigger"], body["requested_by_id"]) == (True, "manual", admin.user_id)
    assert web.get("/api/admin/audit/checks").json()[0]["id"] == body["id"]
    assert events(migrated_db)[-1] == "audit.check_passed"


def _export(web: TestClient, admin: Signed, **filters: object):  # type: ignore[no-untyped-def]
    return web.post("/api/admin/audit/export", json=filters, headers=admin.headers)


def test_export_is_a_csv_with_the_chain_result_and_is_itself_recorded(
    web: TestClient, migrated_db: str
) -> None:
    admin = _admin(web, migrated_db)
    _record(migrated_db, "test.export", details={"note": "ok"})
    resp = _export(web, admin)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"].startswith('attachment; filename="ledgerline-audit-')
    assert resp.headers["x-audit-chain"] == "passed"
    lines = resp.text.splitlines()
    preamble = [line for line in lines if line.startswith("#")]
    assert preamble[0] == "# Ledgerline audit log export"
    assert any(
        line.startswith("# Chain check ") and ": passed: entries 1 to " in line for line in preamble
    )
    table = list(csv.DictReader(io.StringIO("\n".join(lines[len(preamble) :]))))
    assert [r["event"] for r in table][-1] == "test.export"
    assert table[0]["seq"] == "1"
    assert table[0]["prev_hash"] == "0" * 64
    assert all(len(r["entry_hash"]) == 64 for r in table)
    # The export and its check are on the record, after the rows the file contains.
    assert events(migrated_db)[-2:] == ["audit.check_passed", "audit.exported"]


def test_export_neutralises_spreadsheet_formulas(web: TestClient, migrated_db: str) -> None:
    make_user(migrated_db, "evil@firm.in", Role.STAFF, name="=HYPERLINK(1)")
    admin = _admin(web, migrated_db)
    with db_session(migrated_db) as db:
        evil = db.scalar(select(User.id).where(User.email == "evil@firm.in"))
    _record(migrated_db, "test.formula", actor_user_id=evil, target=("client", "+cmd"))
    text = _export(web, admin, event="test.formula").text
    row = next(
        csv.DictReader(
            io.StringIO("\n".join(t for t in text.splitlines() if not t.startswith("#")))
        )
    )
    assert row["actor_name"] == "'=HYPERLINK(1)"
    assert row["target_id"] == "'+cmd"


def test_export_refuses_an_unreasonably_large_file(
    web: TestClient, migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.admin_audit.MAX_EXPORT_ROWS", 1)
    admin = _admin(web, migrated_db)
    resp = _export(web, admin)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "export_too_large"


def test_nothing_offers_to_change_or_remove_an_entry() -> None:
    app = create_app(make_settings())
    audit_routes = [
        (method, route.path)
        for route in api_routes(app)
        if route.path.startswith("/api/admin/audit")
        for method in route.methods or ()
    ]
    assert len(audit_routes) == 6
    assert not [r for r in audit_routes if r[0] in {"PUT", "PATCH", "DELETE"}]
