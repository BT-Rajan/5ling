"""Chunk C06: due and alert dates (BRD FR-PK-02 to FR-PK-04, NFR-AU-01).

Engine cases need no database. Weekdays of the dates used here (2026 to 2028) were checked
against a calendar: e.g. 7 Nov 2026 is a Saturday, 28 Feb 2027 a Sunday, 31 Jul 2027 a Saturday.
"""

from __future__ import annotations

from datetime import date

import pytest
from app.models import Frequency, HolidayRule, PeriodBasis, Role
from app.schedule.engine import (
    DueDay,
    NoWorkingDay,
    Rule,
    add_months,
    apply_holiday_rule,
    periods,
    year_schedule,
)
from fastapi.testclient import TestClient

from tests.audit_helpers import events
from tests.helpers import Signed, make_user, sign_in

FY = PeriodBasis.FINANCIAL_YEAR
CY = PeriodBasis.CALENDAR_YEAR
EARLIER = HolidayRule.EARLIER_WORKING_DAY
AS_IS = HolidayRule.AS_IS


def rule(
    frequency: Frequency = Frequency.MONTHLY,
    *,
    months_after: int = 1,
    day: int = 11,
    overrides: dict[str, DueDay] | None = None,
    lead: int = 4,
    holiday: HolidayRule = EARLIER,
    basis: PeriodBasis = FY,
) -> Rule:
    return Rule(frequency, basis, DueDay(months_after, day), overrides or {}, lead, holiday)


def by_key(r: Rule, year: int, holidays: set[date] | None = None) -> dict[str, tuple[date, date]]:
    return {
        s.period.key: (s.due_date, s.alert_date) for s in year_schedule(r, year, holidays or set())
    }


# --- Done when: a test calendar with a known holiday gives the expected alert date -----------


def test_a_known_holiday_moves_the_alert_to_the_working_day_before() -> None:
    gstr1 = rule()  # monthly, due the 11th of the next month, alert 4 days before
    # October 2026: due Wednesday 11 Nov. 4 days before is Saturday 7 Nov, so the alert moves
    # to Friday 6 Nov; with 6 Nov a holiday it moves again, to Thursday 5 Nov.
    assert by_key(gstr1, 2026)["M7"] == (date(2026, 11, 11), date(2026, 11, 6))
    assert by_key(gstr1, 2026, {date(2026, 11, 6)})["M7"] == (date(2026, 11, 11), date(2026, 11, 5))
    # "As is" leaves both dates where the rule puts them, holiday or not.
    as_is = rule(holiday=AS_IS)
    assert by_key(as_is, 2026, {date(2026, 11, 6)})["M7"] == (date(2026, 11, 11), date(2026, 11, 7))


def test_a_due_date_on_a_holiday_moves_and_the_alert_counts_back_from_the_new_date() -> None:
    # Due Monday 11 May 2026 (April's return). Holidays on 11 and 8 May push it to Thursday 7 May,
    # and the alert is 4 days before that: Sunday 3 May, so Friday 1 May.
    holidays = {date(2026, 5, 11), date(2026, 5, 8)}
    (only,) = [s for s in year_schedule(rule(), 2026, holidays) if s.period.key == "M1"]
    assert (only.due_rule_date, only.due_date) == (date(2026, 5, 11), date(2026, 5, 7))
    assert (only.alert_rule_date, only.alert_date) == (date(2026, 5, 3), date(2026, 5, 1))


# --- Month ends ---------------------------------------------------------------------------------


def test_day_31_means_the_last_day_of_short_months_including_leap_years() -> None:
    month_end = rule(months_after=0, day=31, holiday=AS_IS)
    fy26 = by_key(month_end, 2026)
    assert fy26["M1"][0] == date(2026, 4, 30)  # April has 30 days
    assert fy26["M11"][0] == date(2027, 2, 28)
    assert by_key(month_end, 2027)["M11"][0] == date(2028, 2, 29)  # leap year


def test_a_month_end_on_a_sunday_moves_to_friday() -> None:
    assert by_key(rule(months_after=0, day=31), 2026)["M11"][0] == date(2027, 2, 26)


def test_months_after_crosses_the_year_end() -> None:
    assert add_months(2026, 12, 1) == (2027, 1)
    assert add_months(2026, 11, 14) == (2028, 1)
    # December 2026's return is due 11 January 2027 (a Monday).
    assert by_key(rule(), 2026)["M9"][0] == date(2027, 1, 11)


# --- Quarterly, yearly and the year they are counted in -------------------------------------


def test_quarterly_tds_with_a_different_q4() -> None:
    tds = rule(
        Frequency.QUARTERLY,
        months_after=1,
        day=31,
        overrides={"Q4": DueDay(2, 31)},
        lead=10,
        holiday=AS_IS,
    )
    dues = {k: v[0] for k, v in by_key(tds, 2026).items()}
    assert dues == {
        "Q1": date(2026, 7, 31),
        "Q2": date(2026, 10, 31),
        "Q3": date(2027, 1, 31),
        "Q4": date(2027, 5, 31),
    }
    # With the earlier-working-day rule, the weekend ones move.
    moved = by_key(Rule(**{**tds.__dict__, "holiday_rule": EARLIER}), 2026)
    assert moved["Q2"][0] == date(2026, 10, 30)  # Saturday to Friday
    assert moved["Q3"][0] == date(2027, 1, 29)  # Sunday to Friday


def test_a_due_date_inside_the_period_is_allowed() -> None:
    advance_tax = rule(Frequency.QUARTERLY, months_after=0, day=15, lead=7, holiday=AS_IS)
    assert by_key(advance_tax, 2026)["Q1"] == (date(2026, 6, 15), date(2026, 6, 8))


def test_yearly_income_tax_return_with_21_days_notice() -> None:
    itr = rule(Frequency.YEARLY, months_after=4, day=31, lead=21)
    # FY 2026-27 ends 31 Mar 2027; due 31 Jul 2027 is a Saturday, so Friday 30 Jul; 21 days
    # before that is Friday 9 Jul.
    assert by_key(itr, 2026) == {"Y": (date(2027, 7, 30), date(2027, 7, 9))}


def test_periods_follow_the_financial_or_calendar_year() -> None:
    fy = periods(Frequency.MONTHLY, FY, 2026)
    assert (fy[0].label, fy[0].start, fy[-1].label, fy[-1].end) == (
        "Apr 2026",
        date(2026, 4, 1),
        "Mar 2027",
        date(2027, 3, 31),
    )
    q = periods(Frequency.QUARTERLY, FY, 2026)
    assert [p.label for p in q] == [
        "Q1 2026-27 (Apr-Jun)",
        "Q2 2026-27 (Jul-Sep)",
        "Q3 2026-27 (Oct-Dec)",
        "Q4 2026-27 (Jan-Mar)",
    ]
    cq = periods(Frequency.QUARTERLY, CY, 2026)
    assert (cq[0].key, cq[0].start, cq[0].end) == ("Q1", date(2026, 1, 1), date(2026, 3, 31))
    assert periods(Frequency.YEARLY, FY, 2026)[0].label == "FY 2026-27"
    assert periods(Frequency.YEARLY, CY, 2026)[0].end == date(2026, 12, 31)
    assert len(periods(Frequency.MONTHLY, CY, 2026)) == 12


def test_a_single_month_can_have_its_own_due_date() -> None:
    special = rule(overrides={"M12": DueDay(1, 20)}, holiday=AS_IS)
    dues = by_key(special, 2026)
    assert dues["M12"][0] == date(2027, 4, 20)  # March's return
    assert dues["M11"][0] == date(2027, 3, 11)


# --- Edges -----------------------------------------------------------------------------------


def test_lead_time_zero_alerts_on_the_due_date() -> None:
    assert by_key(rule(lead=0), 2026)["M1"] == (date(2026, 5, 11), date(2026, 5, 11))


def test_a_holiday_list_with_no_working_day_is_an_error_not_a_hang() -> None:
    every_day = {date(2026, 4, 1).fromordinal(date(2026, 4, 1).toordinal() + n) for n in range(60)}
    with pytest.raises(NoWorkingDay):
        apply_holiday_rule(date(2026, 5, 15), EARLIER, every_day)


def test_years_out_of_range_are_refused() -> None:
    with pytest.raises(ValueError, match="year"):
        periods(Frequency.MONTHLY, FY, 1066)


# --- Through the API -------------------------------------------------------------------------

MONTHLY = {"frequency": "monthly", "due": {"months_after": 1, "day": 11}}


@pytest.fixture
def admin(web: TestClient, migrated_db: str) -> Signed:
    make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    return sign_in(web, "boss@firm.in")


@pytest.mark.requires_db
def test_the_preview_uses_the_firms_holidays(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    def october() -> dict[str, object]:
        resp = web.post("/api/admin/filings/preview?year=2026", json=MONTHLY, headers=admin.headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["year_label"], body["lead_days"], body["lead_days_is_default"]) == (
            "FY 2026-27",
            4,
            True,
        )
        assert len(body["rows"]) == 12
        return next(r for r in body["rows"] if r["key"] == "M7")

    assert october()["alert_date"] == "2026-11-06"
    added = web.post(
        "/api/admin/holidays",
        json={"day": "2026-11-06", "name": "Test holiday"},
        headers=admin.headers,
    )
    assert added.status_code == 201
    row = october()
    assert (row["due_date"], row["alert_date"], row["alert_moved_from"]) == (
        "2026-11-11",
        "2026-11-05",
        "2026-11-07",
    )
    assert row["label"] == "Oct 2026"
    assert (
        web.post(
            "/api/admin/holidays",
            json={"day": "2026-11-06", "name": "Again"},
            headers=admin.headers,
        ).status_code
        == 409
    )
    assert web.get(
        "/api/admin/holidays", params={"from_date": "2026-01-01", "to_date": "2026-12-31"}
    ).json() == [{"day": "2026-11-06", "name": "Test holiday"}]
    assert web.delete("/api/admin/holidays/2026-11-06", headers=admin.headers).status_code == 204
    assert october()["alert_date"] == "2026-11-06"
    assert {"holiday.added", "holiday.removed"} <= set(events(migrated_db))


@pytest.mark.requires_db
def test_lead_time_defaults_are_the_administrators_to_set(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    current = web.get("/api/admin/lead-times").json()
    assert (current["monthly_days"], current["quarterly_days"], current["yearly_days"]) == (
        4,
        10,
        21,
    )
    quarterly = {
        "frequency": "quarterly",
        "due": {"months_after": 1, "day": 31},
        "holiday_rule": "as_is",
    }

    def q1_alert() -> str:
        rows = web.post(
            "/api/admin/filings/preview?year=2026", json=quarterly, headers=admin.headers
        ).json()["rows"]
        return str(rows[0]["alert_date"])

    assert q1_alert() == "2026-07-21"  # 10 days before 31 Jul
    changed = web.put(
        "/api/admin/lead-times",
        json={"monthly_days": 4, "quarterly_days": 15, "yearly_days": 21},
        headers=admin.headers,
    )
    assert changed.status_code == 200
    assert changed.json()["updated_by_id"] == admin.user_id
    assert q1_alert() == "2026-07-16"
    # A calendar's own lead time wins over the default (FR-PK-03).
    own = web.post(
        "/api/admin/filings/preview?year=2026",
        json={**quarterly, "lead_days": 3},
        headers=admin.headers,
    )
    assert own.json()["rows"][0]["alert_date"] == "2026-07-28"
    assert (
        web.put(
            "/api/admin/lead-times",
            json={"monthly_days": 91, "quarterly_days": 1, "yearly_days": 1},
            headers=admin.headers,
        ).status_code
        == 422
    )
    assert "lead_times.changed" in events(migrated_db)


@pytest.mark.requires_db
def test_preview_defaults_to_the_current_year_and_rejects_impossible_rules(
    web: TestClient, admin: Signed
) -> None:
    resp = web.post("/api/admin/filings/preview", json=MONTHLY, headers=admin.headers)
    assert resp.status_code == 200
    assert resp.json()["year"] in range(2025, 2101)
    bad = web.post(
        "/api/admin/filings/preview",
        json={
            **MONTHLY,
            "due": {
                "months_after": 1,
                "day": 11,
                "overrides": {"Q1": {"months_after": 1, "day": 1}},
            },
        },
        headers=admin.headers,
    )
    assert bad.status_code == 422
