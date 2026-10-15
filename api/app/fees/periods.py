"""Billing periods (BRD FR-FE-02): monthly, quarterly and yearly follow India's financial year
(April to March); a one-time fee has a single period. Invoices are raised at the start of a
period, for the period, and a client is billed from the period it joined in."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.models import BillingFrequency

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Period:
    key: str  # unique per client: one invoice per period
    label: str
    start: date | None
    end: date | None


def fy_start(day: date) -> date:
    return date(day.year if day.month >= 4 else day.year - 1, 4, 1)


def fy_label(day: date) -> str:
    start = fy_start(day).year
    return f"{start % 100:02d}-{(start + 1) % 100:02d}"


def _month_end(day: date) -> date:
    first_next = date(day.year + day.month // 12, day.month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


ONE_TIME = Period("ONCE", "One-time fee", None, None)


def period_containing(frequency: BillingFrequency, day: date) -> Period:
    if frequency is BillingFrequency.ONE_TIME:
        return ONE_TIME
    if frequency is BillingFrequency.MONTHLY:
        start = date(day.year, day.month, 1)
        return Period(
            f"M{start:%Y-%m}", f"{MONTHS[start.month - 1]} {start.year}", start, _month_end(start)
        )
    fy = fy_start(day)
    if frequency is BillingFrequency.YEARLY:
        end = date(fy.year + 1, 3, 31)
        return Period(f"Y{fy_label(fy)}", f"FY {fy.year}-{(fy.year + 1) % 100:02d}", fy, end)
    quarter = ((day.year * 12 + day.month - 1) - (fy.year * 12 + 3)) // 3  # 0..3
    start = _add_months(fy, 3 * quarter)
    end = _month_end(_add_months(start, 2))
    label = f"{MONTHS[start.month - 1]}-{MONTHS[end.month - 1]} {end.year}"
    return Period(f"Q{fy_label(fy)}-{quarter + 1}", label, start, end)


def periods_due(frequency: BillingFrequency, first_day: date, today: date) -> list[Period]:
    """Every period from the one containing `first_day` to the one containing `today`."""
    if today < first_day:
        return []
    if frequency is BillingFrequency.ONE_TIME:
        return [ONE_TIME]
    out: list[Period] = []
    day = first_day
    while True:
        period = period_containing(frequency, day)
        out.append(period)
        if period.end is None:  # only the one-time period, handled above
            raise RuntimeError("a recurring period without an end")
        day = period.end + timedelta(days=1)
        if day > today:
            return out
