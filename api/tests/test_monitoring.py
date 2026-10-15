"""Chunk C31: monitoring and restore checks (BRD NFR-OB-01, NFR-AV-02)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from app.db import make_engine
from app.files.store import store_from_settings
from app.mail.provider import RecordingProvider, TemporaryFailure
from app.models import JobRun, JobStatus, JobTrigger, OpsAlert
from app.ops.errors import error_counter
from app.ops.monitor import run, unit_failed
from app.ops.restore import backup_age_hours, check
from app.timeutil import utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.conftest import make_settings
from tests.helpers import db_session
from tests.test_alerts import factory

__all__ = ["factory"]  # fixtures re-used from other test modules

pytestmark = pytest.mark.requires_db

SETTINGS = make_settings(tech_owner_email="it@firm.example.in", error_spike_threshold=3)


def failed_job(url: str, minutes_ago: int = 1) -> None:
    with db_session(url) as db:
        db.add(
            JobRun(
                job="cycle_generator",
                trigger=JobTrigger.SCHEDULED,
                status=JobStatus.FAILED,
                started_at=utcnow() - timedelta(minutes=minutes_ago),
                finished_at=utcnow(),
                clients_failed=2,
                error="database went away",
            )
        )
        db.commit()


def test_a_failed_job_reaches_the_technical_owner_on_the_next_run_and_only_once(
    migrated_db: str, factory: sessionmaker[Session]
) -> None:
    failed_job(migrated_db)
    mail = RecordingProvider()
    first = run(factory, SETTINGS, mail)
    assert (first.raised, first.delivered, first.failed) == (1, 1, 0)
    assert mail.sent[0].to == ["it@firm.example.in"]
    assert "Job failed: cycle_generator" in mail.sent[0].subject
    assert "database went away" in mail.sent[0].body
    # The monitor runs every minute: within 5 minutes of the failure, and never again for it.
    again = run(factory, SETTINGS, mail)
    assert (again.raised, again.delivered) == (0, 0)
    assert len(mail.sent) == 1


def test_an_alert_the_mail_server_refuses_is_tried_again(
    migrated_db: str, factory: sessionmaker[Session]
) -> None:
    failed_job(migrated_db)
    down = RecordingProvider(fail_with=TemporaryFailure("mail server unreachable"))
    assert run(factory, SETTINGS, down).failed == 1
    up = RecordingProvider()
    later = run(factory, SETTINGS, up)
    assert (later.raised, later.delivered) == (0, 1)
    with db_session(migrated_db) as db:
        alert = db.scalar(select(OpsAlert))
        assert alert is not None
        assert (alert.delivered, alert.attempts, alert.last_error) == (True, 2, None)


def test_an_error_spike_alerts_once_per_ten_minutes(
    migrated_db: str, factory: sessionmaker[Session]
) -> None:
    engine = make_engine(migrated_db)
    try:
        count = error_counter(engine)
        for _ in range(2):
            count()
        mail = RecordingProvider()
        assert run(factory, SETTINGS, mail).raised == 0  # 2 is under the threshold of 3
        count()
        assert run(factory, SETTINGS, mail).raised == 1
        count()
        assert run(factory, SETTINGS, mail).raised == 0  # the same ten minutes
    finally:
        engine.dispose()
    assert "server errors in 5 minutes" in mail.sent[0].subject


def test_a_failed_service_alerts_at_once(migrated_db: str, factory: sessionmaker[Session]) -> None:
    mail = RecordingProvider()
    tally = unit_failed(factory, SETTINGS, mail, "ledgerline-mailer.service; rm -rf /")
    assert (tally.raised, tally.delivered) == (1, 1)
    assert mail.sent[0].subject.endswith("Service failed: ledgerline-mailer.servicerm-rf")
    no_owner = make_settings()
    assert unit_failed(factory, no_owner, mail, "ledgerline-scan.service").failed == 1


def test_production_needs_a_technical_owner() -> None:
    with pytest.raises(ValueError, match="tech_owner_email"):
        make_settings(env="production", allowed_hosts="app.example.test", tech_owner_email="")


def test_a_restored_database_is_checked(migrated_db: str, factory: sessionmaker[Session]) -> None:
    now = utcnow()
    name = f"ledgerline-{now:%Y%m%d-%H%M%S}.sql.gz.enc"
    assert backup_age_hours(name, now) == pytest.approx(0, abs=0.01)
    assert backup_age_hours("other.sql") is None
    with factory() as db:
        lines = check(db, store_from_settings(make_settings()), name)
    texts = [line.text for line in lines]
    assert lines[0].ok, texts  # schema at the latest migration
    assert lines[1].ok, texts  # audit chain intact
    assert texts[-1].startswith("backup taken 0.0 hours ago")
