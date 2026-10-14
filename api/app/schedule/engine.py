"""The due-date engine (BRD FR-PK-02 to FR-PK-04). Pure functions: rules and holidays in, dates out.

Rules are data the Administrator edits, never code (NFR-MN-01). Dates are calendar dates in India,
not times (NFR-AU-01).

- A period's due date is `day` of the month `months_after` months after the period's last month.
  A day past that month's end means its last day (31 in February is the 28th or 29th).
- The alert date is the due date minus the lead time.
- When either date falls on a Saturday, Sunday or a listed holiday, the calendar's holiday rule
  applies: move to the earlier working day, or keep it as it is. The alert date is counted back
  from the due date as finally set.
"""

from __future__ import annotations

import calendar as _cal
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from app.models import Frequency, HolidayRule, PeriodBasis

WEEKEND = frozenset({5, 6})  # Saturday, Sunday
MAX_SHIFT_DAYS = 31  # a month with no working day is a data error, not something to search past
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class DueDay:
    months_after: int
    day: int


@dataclass(frozen=True)
class Rule:
    """Everything the engine needs from a calendar version, plus the default lead time."""

    frequency: Frequency
    period_basis: PeriodBasis
    due: DueDay
    overrides: Mapping[str, DueDay]
    lead_days: int
    holiday_rule: HolidayRule


@dataclass(frozen=True)
class Period:
    key: str  # M1..M12, Q1..Q4 or Y, counted from the start of the year the calendar uses
    label: str
    start: date
    end: date


@dataclass(frozen=True)
class Scheduled:
    period: Period
    due_date: date
    due_rule_date: date  # before the holiday rule; differs only when the date was moved
    alert_date: date
    alert_rule_date: date
    lead_days: int


class NoWorkingDay(ValueError):
    """Every day in the search window is a weekend or holiday: the holiday list is wrong."""


def add_months(year: int, month: int, months: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + months
    return index // 12, index % 12 + 1


def day_in_month(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, _cal.monthrange(year, month)[1]))


def _year_label(start_year: int, basis: PeriodBasis) -> str:
    if basis is PeriodBasis.FINANCIAL_YEAR:
        return f"{start_year}-{(start_year + 1) % 100:02d}"
    return str(start_year)


def periods(frequency: Frequency, basis: PeriodBasis, year: int) -> list[Period]:
    """The periods of one year. `year` is the year it starts in: FY 2026 is April 2026 to
    March 2027; calendar year 2026 is January to December 2026."""
    if not 1900 <= year <= 2200:
        raise ValueError("year out of range")
    first_month = 4 if basis is PeriodBasis.FINANCIAL_YEAR else 1
    months_per = {Frequency.MONTHLY: 1, Frequency.QUARTERLY: 3, Frequency.YEARLY: 12}[frequency]
    prefix = {Frequency.MONTHLY: "M", Frequency.QUARTERLY: "Q", Frequency.YEARLY: "Y"}[frequency]
    found: list[Period] = []
    for n in range(12 // months_per):
        sy, sm = add_months(year, first_month, n * months_per)
        ey, em = add_months(sy, sm, months_per - 1)
        start, end = date(sy, sm, 1), day_in_month(ey, em, 31)
        if frequency is Frequency.MONTHLY:
            key, label = f"M{n + 1}", f"{_MONTHS[sm - 1]} {sy}"
        elif frequency is Frequency.QUARTERLY:
            key = f"Q{n + 1}"
            label = f"Q{n + 1} {_year_label(year, basis)} ({_MONTHS[sm - 1]}-{_MONTHS[em - 1]})"
        else:
            key = prefix
            label = (
                f"FY {_year_label(year, basis)}"
                if basis is PeriodBasis.FINANCIAL_YEAR
                else str(year)
            )
        found.append(Period(key=key, label=label, start=start, end=end))
    return found


def is_working_day(day: date, holidays: Collection[date]) -> bool:
    return day.weekday() not in WEEKEND and day not in holidays


def apply_holiday_rule(day: date, rule: HolidayRule, holidays: Collection[date]) -> date:
    if rule is HolidayRule.AS_IS:
        return day
    moved = day
    for _ in range(MAX_SHIFT_DAYS + 1):
        if is_working_day(moved, holidays):
            return moved
        moved -= timedelta(days=1)
    raise NoWorkingDay(f"no working day in the {MAX_SHIFT_DAYS} days before {day.isoformat()}")


def rule_due_date(rule: Rule, period: Period) -> date:
    due = rule.overrides.get(period.key, rule.due)
    year, month = add_months(period.end.year, period.end.month, due.months_after)
    return day_in_month(year, month, due.day)


def schedule(rule: Rule, period: Period, holidays: Collection[date]) -> Scheduled:
    due_raw = rule_due_date(rule, period)
    due = apply_holiday_rule(due_raw, rule.holiday_rule, holidays)
    alert_raw = due - timedelta(days=rule.lead_days)
    alert = apply_holiday_rule(alert_raw, rule.holiday_rule, holidays)
    return Scheduled(period, due, due_raw, alert, alert_raw, rule.lead_days)


def year_schedule(rule: Rule, year: int, holidays: Collection[date]) -> list[Scheduled]:
    return [schedule(rule, p, holidays) for p in periods(rule.frequency, rule.period_basis, year)]


def date_window(rule: Rule, year: int) -> tuple[date, date]:
    """The span of dates a year's schedule can touch, for loading only the holidays needed."""
    plist = periods(rule.frequency, rule.period_basis, year)
    furthest = max([rule.due.months_after, *(o.months_after for o in rule.overrides.values())])
    ey, em = add_months(plist[-1].end.year, plist[-1].end.month, furthest)
    latest = day_in_month(ey, em, 31)
    earliest = plist[0].end - timedelta(days=rule.lead_days + 2 * MAX_SHIFT_DAYS)
    return earliest, latest
