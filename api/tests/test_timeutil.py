from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from app.db import UTCDateTime
from app.timeutil import ensure_utc, format_ist, ist_date_of, to_ist


def test_naive_datetime_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        ensure_utc(datetime(2026, 10, 8, 12, 0))


def test_ist_is_plus_five_thirty() -> None:
    dt = datetime(2026, 10, 8, 6, 30, tzinfo=UTC)
    assert to_ist(dt).hour == 12
    assert to_ist(dt).minute == 0


def test_date_rolls_over_at_ist_midnight_not_utc_midnight() -> None:
    # 20:00 UTC on 31 Oct is 01:30 IST on 1 Nov: the Indian calendar date is already November.
    assert ist_date_of(datetime(2026, 10, 31, 20, 0, tzinfo=UTC)) == date(2026, 11, 1)
    # 18:29 UTC is 23:59 IST: still the same Indian day.
    assert ist_date_of(datetime(2026, 10, 31, 18, 29, tzinfo=UTC)) == date(2026, 10, 31)
    assert ist_date_of(datetime(2026, 10, 31, 18, 30, tzinfo=UTC)) == date(2026, 11, 1)


def test_leap_day_and_year_end() -> None:
    assert ist_date_of(datetime(2028, 2, 28, 19, 0, tzinfo=UTC)) == date(2028, 2, 29)
    assert ist_date_of(datetime(2026, 12, 31, 19, 0, tzinfo=UTC)) == date(2027, 1, 1)


def test_other_timezones_convert_correctly() -> None:
    dt = datetime(2026, 10, 8, 0, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert ensure_utc(dt).hour == 5


def test_format_ist() -> None:
    assert format_ist(datetime(2026, 10, 8, 6, 30, tzinfo=UTC)) == "08 Oct 2026, 12:00"


def test_utc_datetime_type_roundtrip_and_refuses_naive() -> None:
    t = UTCDateTime()
    aware = datetime(2026, 10, 8, 12, 0, 0, 123456, tzinfo=UTC)
    stored = t.process_bind_param(aware, None)  # type: ignore[arg-type]
    assert stored is not None
    assert stored.tzinfo is None
    back = t.process_result_value(stored, None)  # type: ignore[arg-type]
    assert back == aware
    with pytest.raises(ValueError, match="naive"):
        t.process_bind_param(datetime(2026, 10, 8), None)  # type: ignore[arg-type]
