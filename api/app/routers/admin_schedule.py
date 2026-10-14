"""Holidays and alert lead-time defaults (BRD FR-PK-03, FR-PK-04). Administrator only."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.errors import ApiHTTPException
from app.models import Holiday, Role
from app.schedule.service import lead_defaults
from app.timeutil import today_ist, utcnow

router = APIRouter(prefix="/api/admin", tags=["admin: holidays and lead times"])

admin_only = require_roles(Role.ADMINISTRATOR)

HolidayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class HolidayIn(BaseModel):
    day: date
    name: HolidayName


class HolidayOut(BaseModel):
    day: date
    name: str


class LeadTimesIn(BaseModel):
    monthly_days: int = Field(ge=0, le=90)
    quarterly_days: int = Field(ge=0, le=90)
    yearly_days: int = Field(ge=0, le=90)


class LeadTimesOut(LeadTimesIn):
    updated_at: datetime
    updated_by_id: int | None


@router.get("/holidays")
def list_holidays(
    _: Principal = Depends(admin_only),  # noqa: B008
    from_date: date | None = None,
    to_date: date | None = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> list[HolidayOut]:
    """Defaults to this calendar year and next."""
    this_year = today_ist().year
    start = from_date or date(this_year, 1, 1)
    end = to_date or date(this_year + 1, 12, 31)
    if start > end:
        raise ApiHTTPException(
            422, "The start date is after the end date.", code="validation_error"
        )
    rows = db.scalars(select(Holiday).where(Holiday.day.between(start, end)).order_by(Holiday.day))
    return [HolidayOut(day=h.day, name=h.name) for h in rows]


@router.post("/holidays", status_code=201)
def add_holiday(
    body: HolidayIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> HolidayOut:
    if not 2000 <= body.day.year <= 2100:
        raise ApiHTTPException(422, "Choose a date between 2000 and 2100.", code="validation_error")
    db.add(Holiday(day=body.day, name=body.name, created_by_id=actor.user_id, created_at=utcnow()))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiHTTPException(409, "That date is already a holiday.", code="duplicate") from None
    audit(
        db,
        "holiday.added",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("holiday", body.day.isoformat()),
        details={"name": body.name},
    )
    db.commit()
    return HolidayOut(day=body.day, name=body.name)


@router.delete("/holidays/{day}", status_code=204)
def remove_holiday(
    day: date,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    holiday = db.get(Holiday, day)
    if holiday is None:
        raise ApiHTTPException(404, "That date is not a holiday.", code="not_found")
    name = holiday.name
    db.delete(holiday)
    audit(
        db,
        "holiday.removed",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("holiday", day.isoformat()),
        details={"name": name},
    )
    db.commit()
    return Response(status_code=204)


def _lead_out(db: Session) -> LeadTimesOut:
    row = lead_defaults(db)
    return LeadTimesOut(
        monthly_days=row.monthly_days,
        quarterly_days=row.quarterly_days,
        yearly_days=row.yearly_days,
        updated_at=row.updated_at,
        updated_by_id=row.updated_by_id,
    )


@router.get("/lead-times")
def get_lead_times(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> LeadTimesOut:
    return _lead_out(db)


@router.put("/lead-times")
def set_lead_times(
    body: LeadTimesIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> LeadTimesOut:
    """Default alert lead times, for calendars that do not set their own (FR-PK-03)."""
    row = lead_defaults(db)
    before = {
        "monthly": row.monthly_days,
        "quarterly": row.quarterly_days,
        "yearly": row.yearly_days,
    }
    row.monthly_days = body.monthly_days
    row.quarterly_days = body.quarterly_days
    row.yearly_days = body.yearly_days
    row.updated_by_id = actor.user_id
    row.updated_at = utcnow()
    audit(
        db,
        "lead_times.changed",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        details={
            "before": [before["monthly"], before["quarterly"], before["yearly"]],
            "after": [body.monthly_days, body.quarterly_days, body.yearly_days],
        },
    )
    db.commit()
    return _lead_out(db)
