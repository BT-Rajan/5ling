"""The cycle generator (BRD FR-CY-01, FR-CY-04, FR-CY-05, CR-02).

For each client that is not closed, it makes sure a cycle (with its FILING ticket) exists for
every period of every filing in the client's package version that starts between the start of
the current year (as each calendar counts years) and 12 months from today. Due and alert dates come from the
filing's current published calendar (FR-PK-05: calendar changes apply to cycles made from then on).

It is safe to run twice, or at the same time as itself:
- only missing cycles are made, and the database refuses a second cycle for the same client,
  filing and period start, so a race loses quietly instead of duplicating;
- one run at a time holds a MySQL named lock; a second run records itself as skipped;
- each client is done in its own transaction, so one failure does not undo or block the rest.

Every run is recorded in job_runs. A run with failures is logged at CRITICAL and shown to every
Administrator until a later run succeeds (FR-CY-05: told the same day); running it again is
always safe.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.cycles.extensions import AlertDates, extensions_for
from app.cycles.service import create_cycle
from app.models import (
    CalendarVersion,
    Client,
    ClientStatus,
    Cycle,
    DueDateChange,
    Filing,
    JobRun,
    JobStatus,
    JobTrigger,
    PackageFiling,
    PeriodBasis,
)
from app.schedule.engine import Scheduled, add_months, day_in_month
from app.schedule.service import current_year, rule_for, schedule_year
from app.timeutil import today_ist, utcnow

log = logging.getLogger("ledgerline.jobs")

JOB_NAME = "cycle_generator"
HORIZON_MONTHS = 12
_LOCK = "ledgerline.cycle_generator"


def horizon(today: date) -> date:
    """12 months from today: periods starting before this are planned."""
    year, month = add_months(today.year, today.month, HORIZON_MONTHS)
    return day_in_month(year, month, today.day)


def year_start(basis: PeriodBasis, today: date) -> date:
    first = current_year(basis, today)
    return date(first, 4, 1) if basis is PeriodBasis.FINANCIAL_YEAR else date(first, 1, 1)


def periods_wanted(db: Session, calendar: CalendarVersion, today: date) -> list[Scheduled]:
    """Every period from the start of the current year to the horizon, with its dates."""
    rule = rule_for(db, calendar)
    first = current_year(rule.period_basis, today)
    start, end = year_start(rule.period_basis, today), horizon(today)
    wanted: list[Scheduled] = []
    for year in range(first, first + 3):  # the horizon is never more than two years on
        # Up to, not including, a period starting exactly 12 months on: a monthly client created
        # on 1 April gets the 12 cycles of the year (FR-CY-01's acceptance test).
        wanted.extend(s for s in schedule_year(db, rule, year) if start <= s.period.start < end)
    return wanted


def _current_calendars(db: Session, filing_ids: list[int]) -> dict[int, CalendarVersion]:
    found: dict[int, CalendarVersion] = {}
    rows = db.scalars(
        select(CalendarVersion)
        .where(CalendarVersion.filing_id.in_(filing_ids), CalendarVersion.published_at.is_not(None))
        .order_by(CalendarVersion.filing_id, CalendarVersion.version.desc())
    )
    for cv in rows:
        found.setdefault(cv.filing_id, cv)
    return found


def generate_for_client(db: Session, client: Client, today: date) -> int:
    """Make the missing cycles for one client, in `db`'s transaction. Returns how many."""
    if client.status is ClientStatus.CLOSED:
        return 0  # FR-CL-04: closed clients get no new cycles
    filing_ids = list(
        db.scalars(
            select(PackageFiling.filing_id).where(
                PackageFiling.package_version_id == client.package_version_id
            )
        )
    )
    if not filing_ids:
        return 0
    calendars = _current_calendars(db, filing_ids)
    existing = {
        (f, s)
        for f, s in db.execute(
            select(Cycle.filing_id, Cycle.period_start).where(Cycle.client_pk == client.id)
        )
    }
    created = 0
    alerts: AlertDates | None = None
    for filing_id in filing_ids:
        calendar = calendars.get(filing_id)
        filing = db.get(Filing, filing_id)
        if calendar is None or filing is None:
            continue  # a published package always has published calendars; nothing to plan
        missing = [
            p
            for p in periods_wanted(db, calendar, today)
            if (filing_id, p.period.start) not in existing
        ]
        if not missing:
            continue
        extended = extensions_for(db, filing_id)
        for planned in missing:
            due, alert = planned.due_date, planned.alert_date
            extension = extended.get(planned.period.start)
            if extension is not None and extension.new_due_date <= due:
                extension = None
            if extension is not None:
                # FR-PK-06: the government moved this period's date after it was announced.
                if alerts is None:
                    alerts = AlertDates(db)
                due = extension.new_due_date
                alert = alerts.alert_for(calendar.id, due)
            cycle = create_cycle(
                db,
                client=client,
                filing=filing,
                calendar_version=calendar,
                package_version_id=client.package_version_id,
                period_key=planned.period.key,
                period_label=planned.period.label,
                period_start=planned.period.start,
                period_end=planned.period.end,
                due_date=due,
                alert_date=alert,
            )
            if extension is not None:
                db.add(
                    DueDateChange(
                        extension_id=extension.id,
                        cycle_id=cycle.id,
                        old_due_date=planned.due_date,
                        old_alert_date=planned.alert_date,
                        new_due_date=due,
                        new_alert_date=alert,
                    )
                )
            created += 1
    return created


@dataclass
class _Tally:
    checked: int = 0
    failed: int = 0
    created: int = 0


def _finish(
    factory: sessionmaker[Session], run_id: int, status: JobStatus, tally: _Tally, error: str | None
) -> JobRun:
    with factory() as db:
        run = db.get(JobRun, run_id)
        if run is None:
            raise RuntimeError("job run row vanished")
        run.status = status
        run.finished_at = utcnow()
        run.clients_checked = tally.checked
        run.clients_failed = tally.failed
        run.cycles_created = tally.created
        run.error = error[:500] if error else None
        audit(
            db,
            "job.cycle_generator_failed"
            if status is JobStatus.FAILED
            else "job.cycle_generator_ran",
            actor_user_id=run.requested_by_id,
            details={
                "run": run.id,
                "trigger": run.trigger,
                "status": status,
                "clients_checked": tally.checked,
                "clients_failed": tally.failed,
                "cycles_created": tally.created,
            },
        )
        db.commit()
        return run


def run(
    factory: sessionmaker[Session],
    trigger: JobTrigger,
    *,
    today: date | None = None,
    client_pks: Iterable[int] | None = None,
    requested_by_id: int | None = None,
    lock_wait_seconds: int = 0,
) -> JobRun:
    """Run the generator for every client (or the given ones) and record how it went.

    If another run holds the lock, wait up to `lock_wait_seconds`, then record this one skipped.
    """
    today = today or today_ist()
    with factory() as db:
        job = JobRun(
            job=JOB_NAME,
            trigger=trigger,
            requested_by_id=requested_by_id,
            status=JobStatus.RUNNING,
            started_at=utcnow(),
        )
        db.add(job)
        db.commit()
        run_id = job.id

    tally = _Tally()
    failures: list[str] = []
    with factory() as lock_db:
        got = lock_db.scalar(
            text("SELECT GET_LOCK(:name, :wait)"), {"name": _LOCK, "wait": lock_wait_seconds}
        )
        if got != 1:
            log.info("cycle generator already running", extra={"ctx": {"run": run_id}})
            return _finish(factory, run_id, JobStatus.SKIPPED, tally, "another run was in progress")
        try:
            with factory() as db:
                stmt = select(Client.id).where(Client.status != ClientStatus.CLOSED)
                if client_pks is not None:
                    stmt = select(Client.id).where(Client.id.in_(list(client_pks)))
                pks = list(db.scalars(stmt.order_by(Client.id)))
            for pk in pks:
                tally.checked += 1
                with factory() as db:
                    client = db.scalar(select(Client).where(Client.id == pk).with_for_update())
                    if client is None:
                        continue
                    try:
                        made = generate_for_client(db, client, today)
                        db.commit()
                        tally.created += made
                    except IntegrityError:
                        # Another writer made the same cycle a moment ago: nothing is missing.
                        db.rollback()
                    except Exception as exc:
                        db.rollback()
                        tally.failed += 1
                        failures.append(f"client {client.client_id}: {type(exc).__name__}")
                        log.exception(
                            "cycle generator failed for a client",
                            extra={"ctx": {"run": run_id, "client_id": client.client_id}},
                        )
        finally:
            lock_db.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK})

    status = JobStatus.FAILED if tally.failed else JobStatus.OK
    result = _finish(factory, run_id, status, tally, "; ".join(failures) or None)
    facts = {
        "run": run_id,
        "trigger": trigger.value,
        "clients_checked": tally.checked,
        "clients_failed": tally.failed,
        "cycles_created": tally.created,
    }
    if tally.failed:
        log.critical("CYCLE GENERATOR FAILED", extra={"ctx": facts})
    else:
        log.info("cycle generator ran", extra={"ctx": facts})
    return result


def run_quietly(
    factory: sessionmaker[Session],
    trigger: JobTrigger,
    *,
    client_pks: Iterable[int],
    requested_by_id: int | None,
) -> None:
    """Right after a client is saved: try now, but never fail the request. The daily run and the
    recorded failure (shown to the Administrator) cover anything missed."""
    try:
        run(
            factory,
            trigger,
            client_pks=client_pks,
            requested_by_id=requested_by_id,
            lock_wait_seconds=30,  # let a daily run in progress finish first
        )
    except Exception:
        log.exception("cycle generator could not run after a client change")
