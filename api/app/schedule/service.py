"""The engine fed from the database: holidays, lead-time defaults and calendar versions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    CalendarVersion,
    Frequency,
    Holiday,
    HolidayRule,
    LeadTimeDefaults,
    PeriodBasis,
)
from app.schedule.engine import DueDay, Rule, Scheduled, date_window, year_schedule
from app.timeutil import today_ist


def lead_defaults(db: Session) -> LeadTimeDefaults:
    row = db.get(LeadTimeDefaults, 1)
    if row is None:  # seeded by migration 0007; its absence is a broken database
        raise RuntimeError("lead_time_defaults row is missing")
    return row


def default_lead_days(defaults: LeadTimeDefaults, frequency: Frequency) -> int:
    return {
        Frequency.MONTHLY: defaults.monthly_days,
        Frequency.QUARTERLY: defaults.quarterly_days,
        Frequency.YEARLY: defaults.yearly_days,
    }[frequency]


def make_rule(
    *,
    frequency: Frequency,
    period_basis: PeriodBasis,
    months_after: int,
    day: int,
    overrides: Mapping[str, Mapping[str, Any]],
    lead_days: int | None,
    holiday_rule: HolidayRule,
    defaults: LeadTimeDefaults,
) -> Rule:
    return Rule(
        frequency=frequency,
        period_basis=period_basis,
        due=DueDay(months_after, day),
        overrides={k: DueDay(int(v["months_after"]), int(v["day"])) for k, v in overrides.items()},
        lead_days=lead_days if lead_days is not None else default_lead_days(defaults, frequency),
        holiday_rule=holiday_rule,
    )


def rule_for(db: Session, version: CalendarVersion) -> Rule:
    return make_rule(
        frequency=version.frequency,
        period_basis=version.period_basis,
        months_after=version.due_months_after,
        day=version.due_day,
        overrides=version.due_overrides or {},
        lead_days=version.lead_days,
        holiday_rule=version.holiday_rule,
        defaults=lead_defaults(db),
    )


def holidays_between(db: Session, start: date, end: date) -> set[date]:
    return set(db.scalars(select(Holiday.day).where(Holiday.day.between(start, end))))


def current_year(basis: PeriodBasis, today: date | None = None) -> int:
    """The year (as `periods` counts it) that today falls in."""
    today = today or today_ist()
    if basis is PeriodBasis.FINANCIAL_YEAR and today.month < 4:
        return today.year - 1
    return today.year


def schedule_year(db: Session, rule: Rule, year: int) -> list[Scheduled]:
    start, end = date_window(rule, year)
    return year_schedule(rule, year, holidays_between(db, start, end))
