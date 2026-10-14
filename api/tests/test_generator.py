"""Chunk C10: the cycle generator (BRD FR-CY-01, FR-CY-04, FR-CY-05, FR-CL-04, CR-02)."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from app.cli import main
from app.config import get_settings
from app.cycles import generator
from app.cycles.generator import horizon, run, year_start
from app.db import make_engine, make_session_factory
from app.models import Client, Cycle, JobStatus, JobTrigger, PeriodBasis, Ticket
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from tests.audit_helpers import events
from tests.conftest import GOOD_SECRET
from tests.helpers import Signed, db_session
from tests.test_clients import _ok, admin, client_body, create, publish_package

__all__ = ["admin"]  # the fixture, re-used from test_clients

APRIL_1 = date(2026, 4, 1)


def test_the_window_runs_from_the_year_start_to_12_months_on() -> None:
    assert horizon(date(2026, 10, 14)) == date(2027, 10, 14)
    assert horizon(date(2027, 1, 31)) == date(2028, 1, 31)
    assert year_start(PeriodBasis.FINANCIAL_YEAR, date(2026, 10, 14)) == date(2026, 4, 1)
    assert year_start(PeriodBasis.FINANCIAL_YEAR, date(2027, 2, 1)) == date(2026, 4, 1)
    assert year_start(PeriodBasis.CALENDAR_YEAR, date(2027, 2, 1)) == date(2027, 1, 1)


# --- With a database -------------------------------------------------------------------------


@pytest.fixture
def factory(migrated_db: str) -> Iterator[sessionmaker[Session]]:
    engine = make_engine(migrated_db)
    yield make_session_factory(engine)
    engine.dispose()


def counts(url: str) -> tuple[int, int]:
    with db_session(url) as db:
        cycles = db.scalar(select(func.count()).select_from(Cycle)) or 0
        tickets = db.scalar(select(func.count()).select_from(Ticket)) or 0
    return cycles, tickets


def periods_of(url: str, client_id: int) -> list[str]:
    with db_session(url) as db:
        pk = db.scalar(select(Client.id).where(Client.client_id == client_id))
        return [
            c.period_label
            for c in db.scalars(
                select(Cycle).where(Cycle.client_pk == pk).order_by(Cycle.period_start)
            )
        ]


def new_client(web: TestClient, admin: Signed, package: int, **over: Any) -> int:
    return int(
        _ok(create(web, admin, client_body(package, owner_id=admin.user_id, **over)), 201)[
            "client_id"
        ]
    )


def wipe_cycles(url: str) -> None:
    """Undo what the save-time run made, so a test can run the generator on a chosen day."""
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in (
            "due_date_changes",
            "due_date_extensions",
            "cycle_stage_changes",
            "state_events",
            "cycles",
            "tickets",
            "ticket_counter",
            "job_runs",
        ):
            conn.execute(text(f"TRUNCATE TABLE {table}"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
    engine.dispose()


# --- Done when: running the job twice creates zero duplicates; a forced failure alerts the
# Administrator ---------------------------------------------------------------------------------


@pytest.mark.requires_db
def test_running_twice_creates_nothing_the_second_time(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    cid = new_client(web, admin, pid)
    wipe_cycles(migrated_db)

    first = run(factory, JobTrigger.SCHEDULED, today=APRIL_1)
    assert (first.status, first.cycles_created) == (JobStatus.OK, 12)
    # FR-CY-01: a new monthly client shows 12 monthly cycles with correct periods.
    assert periods_of(migrated_db, cid) == [
        f"{m} {y}"
        for m, y in [
            ("Apr", 2026),
            ("May", 2026),
            ("Jun", 2026),
            ("Jul", 2026),
            ("Aug", 2026),
            ("Sep", 2026),
            ("Oct", 2026),
            ("Nov", 2026),
            ("Dec", 2026),
            ("Jan", 2027),
            ("Feb", 2027),
            ("Mar", 2027),
        ]
    ]
    second = run(factory, JobTrigger.SCHEDULED, today=APRIL_1)
    assert (second.status, second.cycles_created) == (JobStatus.OK, 0)
    assert counts(migrated_db) == (12, 12)  # every cycle has exactly one ticket
    # A month later the window has moved on by one period, and only that one is added.
    later = run(factory, JobTrigger.SCHEDULED, today=date(2026, 5, 1))
    assert later.cycles_created == 1
    assert periods_of(migrated_db, cid)[-1] == "Apr 2027"


@pytest.mark.requires_db
def test_a_forced_failure_alerts_the_administrator_and_a_rerun_repairs_it(
    web: TestClient,
    admin: Signed,
    migrated_db: str,
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    pid = publish_package(web, admin)
    broken = new_client(web, admin, pid)
    fine = new_client(web, admin, pid, pan="ABCPF1234G")
    wipe_cycles(migrated_db)

    real = generator.generate_for_client

    def fail_for_one(db: Session, client: Client, today: date) -> int:
        if client.client_id == broken:
            raise RuntimeError("forced failure")
        return real(db, client, today)

    monkeypatch.setattr(generator, "generate_for_client", fail_for_one)
    with caplog.at_level(logging.CRITICAL, logger="ledgerline.jobs"):
        failed = run(factory, JobTrigger.SCHEDULED, today=APRIL_1)
    assert (failed.status, failed.clients_failed, failed.cycles_created) == (
        JobStatus.FAILED,
        1,
        12,
    )
    assert failed.error == f"client {broken}: RuntimeError"
    assert any(r.levelno == logging.CRITICAL for r in caplog.records)
    assert periods_of(migrated_db, broken) == []
    assert len(periods_of(migrated_db, fine)) == 12  # one failure does not stop the rest

    # The Administrator sees it on every screen until a run succeeds.
    status = _ok(web.get("/api/admin/jobs/cycle-generator"))
    assert status["latest"]["status"] == "failed"
    assert "job.cycle_generator_failed" in events(migrated_db)

    monkeypatch.setattr(generator, "generate_for_client", real)
    again = _ok(web.post("/api/admin/jobs/cycle-generator/run", headers=admin.headers))
    assert again["status"] == "ok"
    assert len(periods_of(migrated_db, broken)) >= 12
    assert _ok(web.get("/api/admin/jobs/cycle-generator"))["latest"]["status"] == "ok"


@pytest.mark.requires_db
def test_closed_clients_get_no_new_cycles(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    cid = new_client(web, admin, pid)
    wipe_cycles(migrated_db)
    _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"status": "closed", "reason": "Engagement ended"},
            headers=admin.headers,
        )
    )
    result = run(factory, JobTrigger.SCHEDULED, today=APRIL_1)
    assert (result.status, result.cycles_created) == (JobStatus.OK, 0)
    assert periods_of(migrated_db, cid) == []


# --- Behaviour ---------------------------------------------------------------------------------


@pytest.mark.requires_db
def test_new_clients_get_their_cycles_when_saved(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    cid = new_client(web, admin, pid)
    tickets = _ok(web.get(f"/api/clients/{cid}/tickets"))
    assert len(tickets) >= 12
    assert all(t["stage"] == "scheduled" and t["code"].startswith("TKT-") for t in tickets)
    runs = _ok(web.get("/api/admin/jobs/cycle-generator"))["recent"]
    assert runs[0]["trigger"] == "client_created"


@pytest.mark.requires_db
def test_new_cycles_use_the_calendar_published_at_the_time(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid)
    wipe_cycles(migrated_db)
    run(factory, JobTrigger.SCHEDULED, today=APRIL_1)
    filing_id = _ok(web.get(f"/api/admin/packages/{pid}"))["versions"][0]["filings"][0]["filing_id"]
    cal = f"/api/admin/filings/{filing_id}/calendar"
    _ok(
        web.put(
            f"{cal}/draft",
            json={"frequency": "monthly", "due": {"months_after": 1, "day": 20}},
            headers=admin.headers,
        )
    )
    _ok(web.post(f"{cal}/publish", headers=admin.headers))
    run(factory, JobTrigger.SCHEDULED, today=date(2026, 5, 1))  # adds April 2027
    with db_session(migrated_db) as db:
        rows = list(db.scalars(select(Cycle).order_by(Cycle.period_start)))
    assert rows[0].due_date.day == 11  # made before: unchanged (FR-PK-05)
    assert rows[-1].due_date == date(2027, 5, 20)  # made after: the new rule
    assert rows[-1].calendar_version_id != rows[0].calendar_version_id


@pytest.mark.requires_db
def test_a_second_run_at_the_same_time_steps_aside(
    web: TestClient, admin: Signed, migrated_db: str, factory: sessionmaker[Session]
) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid)
    wipe_cycles(migrated_db)
    with factory() as holder:
        assert holder.scalar(text("SELECT GET_LOCK('ledgerline.cycle_generator', 0)")) == 1
        skipped = run(factory, JobTrigger.MANUAL, today=APRIL_1)
        holder.execute(text("SELECT RELEASE_LOCK('ledgerline.cycle_generator')"))
    assert (skipped.status, skipped.cycles_created) == (JobStatus.SKIPPED, 0)
    assert counts(migrated_db) == (0, 0)
    # A skipped run is not a failure to warn about.
    assert _ok(web.get("/api/admin/jobs/cycle-generator"))["latest"] is None


@pytest.fixture
def cli_env(migrated_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv("LL_DATABASE_URL", migrated_db)
    monkeypatch.setenv("LL_SECRET_KEY", GOOD_SECRET)
    get_settings.cache_clear()
    yield migrated_db
    get_settings.cache_clear()


@pytest.mark.requires_db
def test_the_daily_job_reports_by_exit_code(
    web: TestClient,
    admin: Signed,
    cli_env: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pid = publish_package(web, admin)
    new_client(web, admin, pid)
    assert main(["generate-cycles"]) == 0
    assert (
        "Cycle generator ran: 0 cycles created for 1 clients (0 failed)." in capsys.readouterr().out
    )

    def boom(db: Session, client: Client, today: date) -> int:
        raise ValueError("no")

    monkeypatch.setattr(generator, "generate_for_client", boom)
    assert main(["generate-cycles"]) == 1
    assert "CYCLE GENERATOR FAILED" in capsys.readouterr().err
