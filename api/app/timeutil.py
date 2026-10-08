"""Time rules (BRD NFR-AU-01): store UTC, show India time, due dates are dates not times."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    """Return value as UTC. Naive datetimes are refused: they are how off-by-one-day bugs start."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("naive datetime refused; pass a timezone-aware value")
    return value.astimezone(UTC)


def to_ist(value: datetime) -> datetime:
    return ensure_utc(value).astimezone(IST)


def ist_date_of(value: datetime) -> date:
    """The calendar date in India at the moment `value`."""
    return to_ist(value).date()


def today_ist() -> date:
    return ist_date_of(utcnow())


def format_ist(value: datetime, fmt: str = "%d %b %Y, %H:%M") -> str:
    return to_ist(value).strftime(fmt)
