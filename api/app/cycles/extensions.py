"""Government due-date extensions (BRD FR-PK-06).

The Administrator picks a filing, a period and the new due date. Every open cycle for that filing
and period whose due date is earlier moves to the new date, and its alert date is counted back
again with the lead time and holiday rule of the calendar version it was built from. Filed,
acknowledged and closed cycles keep their dates: the extension came after the work was done.

The new due date is used as given (the government names an exact day); only the alert date is
moved by the holiday rule.

Cycles the generator makes later for the same filing and period take the extended date too, so a
client added after an extension is not alerted against the old date.

Locking: applying an extension locks the filing row first. The generator takes a shared lock on
the same row before reading extensions, so it never builds a cycle from a date being replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CalendarVersion,
    Client,
    Cycle,
    DueDateChange,
    DueDateExtension,
    Filing,
    Stage,
    Ticket,
)
from app.schedule.engine import MAX_SHIFT_DAYS, Rule, apply_holiday_rule
from app.schedule.service import holidays_between, rule_for

OPEN_STAGES = tuple(s for s in Stage if list(Stage).index(s) < list(Stage).index(Stage.FILED))


@dataclass(frozen=True)
class Planned:
    cycle: Cycle
    ticket_number: int
    client_id: int
    client_name: str
    new_due_date: date
    new_alert_date: date


@dataclass(frozen=True)
class Plan:
    period_label: str
    period_end: date
    changes: list[Planned]
    unchanged: int  # open cycles already due on or after the new date


class ExtensionRefused(ValueError):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class AlertDates:
    """Alert dates for new due dates, one calendar rule and holiday lookup per calendar version."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._rules: dict[int, Rule] = {}
        self._holidays: dict[tuple[date, date], set[date]] = {}

    def rule(self, calendar_version_id: int) -> Rule:
        found = self._rules.get(calendar_version_id)
        if found is None:
            version = self._db.get(CalendarVersion, calendar_version_id)
            if version is None:  # a foreign key prevents it
                raise RuntimeError("cycle refers to a missing calendar version")
            found = self._rules[calendar_version_id] = rule_for(self._db, version)
        return found

    def alert_for(self, calendar_version_id: int, due: date) -> date:
        rule = self.rule(calendar_version_id)
        alert = due - timedelta(days=rule.lead_days)
        window = (alert - timedelta(days=MAX_SHIFT_DAYS + 1), alert)
        holidays = self._holidays.get(window)
        if holidays is None:
            holidays = self._holidays[window] = holidays_between(self._db, *window)
        return apply_holiday_rule(alert, rule.holiday_rule, holidays)


def open_periods(db: Session, filing_id: int) -> list[tuple[date, str, int, date, date]]:
    """Periods with open cycles: (start, label, open cycles, earliest due, latest due)."""
    rows = db.execute(
        select(
            Cycle.period_start,
            func.min(Cycle.period_label),
            func.count(),
            func.min(Cycle.due_date),
            func.max(Cycle.due_date),
        )
        .where(Cycle.filing_id == filing_id, Cycle.stage.in_(OPEN_STAGES))
        .group_by(Cycle.period_start)
        .order_by(Cycle.period_start)
    ).all()
    return [(s, label, int(n), lo, hi) for s, label, n, lo, hi in rows]


def plan(
    db: Session, filing_id: int, period_start: date, new_due: date, *, lock: bool = False
) -> Plan:
    """What applying the extension would change. With `lock`, the cycles are locked for update
    (and read as committed, not from the transaction's snapshot)."""
    any_cycle = db.execute(
        select(Cycle.period_label, Cycle.period_end)
        .where(Cycle.filing_id == filing_id, Cycle.period_start == period_start)
        .limit(1)
    ).first()
    if any_cycle is None:
        raise ExtensionRefused("no_cycles", "No client has a cycle for that filing and period.")
    label, period_end = any_cycle
    if new_due <= period_end:
        raise ExtensionRefused(
            "bad_date", f"The new due date must be after the period ends ({period_end:%d %b %Y})."
        )
    if new_due > period_end + timedelta(days=3 * 366):
        raise ExtensionRefused("bad_date", "The new due date is more than three years away.")

    stmt = (
        select(Cycle, Ticket.number, Client.client_id, Client.legal_name)
        .join(Ticket, Ticket.id == Cycle.ticket_id)
        .join(Client, Client.id == Cycle.client_pk)
        .where(
            Cycle.filing_id == filing_id,
            Cycle.period_start == period_start,
            Cycle.stage.in_(OPEN_STAGES),
        )
        .order_by(Cycle.id)
    )
    if lock:
        stmt = stmt.with_for_update(of=Cycle)
    alerts = AlertDates(db)
    changes: list[Planned] = []
    unchanged = 0
    for cycle, number, client_id, name in db.execute(stmt):
        if cycle.due_date >= new_due:
            unchanged += 1
            continue
        changes.append(
            Planned(
                cycle=cycle,
                ticket_number=number,
                client_id=client_id,
                client_name=name,
                new_due_date=new_due,
                new_alert_date=alerts.alert_for(cycle.calendar_version_id, new_due),
            )
        )
    return Plan(period_label=label, period_end=period_end, changes=changes, unchanged=unchanged)


def apply(
    db: Session,
    *,
    filing: Filing,
    period_start: date,
    new_due: date,
    reason: str,
    expected_changes: int,
    actor_id: int,
) -> tuple[DueDateExtension, Plan]:
    """Apply the extension in `db`'s transaction. The caller audits and commits.

    `expected_changes` is how many cycles the preview showed: if the count differs now (a cycle
    was filed, or another extension landed), nothing is changed and the preview must be redone.
    """
    db.execute(select(Filing.id).where(Filing.id == filing.id).with_for_update())
    planned = plan(db, filing.id, period_start, new_due, lock=True)
    if len(planned.changes) != expected_changes:
        raise ExtensionRefused(
            "preview_stale",
            f"{len(planned.changes)} cycles would change now, not {expected_changes}. "
            "Preview again before applying.",
            status=409,
        )
    if not planned.changes:
        raise ExtensionRefused(
            "nothing_to_change", "No open cycle for that period is due before the new date."
        )
    extension = DueDateExtension(
        filing_id=filing.id,
        period_start=period_start,
        period_label=planned.period_label,
        new_due_date=new_due,
        reason=reason,
        cycles_changed=len(planned.changes),
        created_by_id=actor_id,
    )
    db.add(extension)
    db.flush()
    for p in planned.changes:
        db.add(
            DueDateChange(
                extension_id=extension.id,
                cycle_id=p.cycle.id,
                old_due_date=p.cycle.due_date,
                old_alert_date=p.cycle.alert_date,
                new_due_date=p.new_due_date,
                new_alert_date=p.new_alert_date,
            )
        )
        p.cycle.due_date = p.new_due_date
        p.cycle.alert_date = p.new_alert_date
    return extension, planned


def extensions_for(db: Session, filing_id: int) -> dict[date, DueDateExtension]:
    """The furthest extension of each period of a filing, for the generator.

    Takes a shared lock on the filing row (so an extension being applied finishes first) and
    reads the extensions as committed, not from the transaction's snapshot.
    """
    db.execute(select(Filing.id).where(Filing.id == filing_id).with_for_update(read=True))
    latest: dict[date, DueDateExtension] = {}
    for ext in db.scalars(
        select(DueDateExtension)
        .where(DueDateExtension.filing_id == filing_id)
        .order_by(DueDateExtension.id)
        .with_for_update(read=True)
    ):
        held = latest.get(ext.period_start)
        if held is None or ext.new_due_date > held.new_due_date:
            latest[ext.period_start] = ext
    return latest
