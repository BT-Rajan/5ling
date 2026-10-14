"""Filing types and their calendars (BRD FR-PK-02 to FR-PK-05). Administrator only.

A calendar is edited as a draft and then published. Publishing never changes an earlier version;
cycles already built keep the version they were built from (chunk C09 records it).
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.catalog import MAX_CHECKLIST, ChecklistEntry, next_version, status_of, unique_labels
from app.errors import ApiHTTPException
from app.models import CalendarVersion, Filing, Frequency, HolidayRule, PeriodBasis, Role
from app.schedule.engine import NoWorkingDay
from app.schedule.service import current_year, lead_defaults, make_rule, schedule_year
from app.timeutil import utcnow

router = APIRouter(prefix="/api/admin/filings", tags=["admin: filings and calendars"])

admin_only = require_roles(Role.ADMINISTRATOR)

_CODE = re.compile(r"^[A-Z0-9][A-Z0-9 ./()-]{0,31}$")
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]

# Period names a calendar may give its own due date, by frequency. Counted within the year the
# calendar uses: with financial-year handling, M1 is April and Q1 is April to June.
PERIOD_KEYS: dict[Frequency, frozenset[str]] = {
    Frequency.MONTHLY: frozenset(f"M{n}" for n in range(1, 13)),
    Frequency.QUARTERLY: frozenset(f"Q{n}" for n in range(1, 5)),
    Frequency.YEARLY: frozenset(),
}


class DueDay(BaseModel):
    months_after: int = Field(ge=0, le=12)
    day: int = Field(ge=1, le=31)


class DueRule(DueDay):
    """Due on `day` of the month `months_after` months after the period's last month."""

    overrides: dict[str, DueDay] = Field(default_factory=dict)


class CalendarIn(BaseModel):
    frequency: Frequency
    period_basis: PeriodBasis = PeriodBasis.FINANCIAL_YEAR
    due: DueRule
    lead_days: int | None = Field(default=None, ge=0, le=90)  # None: the frequency's default
    holiday_rule: HolidayRule = HolidayRule.EARLIER_WORKING_DAY
    notes: Text | None = None

    @model_validator(mode="after")
    def _overrides_fit_frequency(self) -> CalendarIn:
        allowed = PERIOD_KEYS[self.frequency]
        unknown = sorted(set(self.due.overrides) - allowed)
        if unknown:
            hint = ", ".join(sorted(allowed, key=lambda k: int(k[1:]))) or "none for yearly filings"
            raise ValueError(f"Unknown period {', '.join(unknown)}; use {hint}.")
        return self


class CalendarOut(BaseModel):
    id: int
    version: int
    status: str
    frequency: Frequency
    period_basis: PeriodBasis
    due: DueRule
    lead_days: int | None
    holiday_rule: HolidayRule
    notes: str | None
    created_at: datetime
    created_by_id: int
    published_at: datetime | None
    published_by_id: int | None


class FilingIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    name: Name
    description: Text | None = None
    default_checklist: list[ChecklistEntry] = Field(default_factory=list, max_length=MAX_CHECKLIST)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        value = " ".join(value.upper().split())
        if not _CODE.match(value):
            raise ValueError("Use capital letters, digits, spaces and . / - ( ) only, e.g. GSTR-1.")
        return value

    @field_validator("default_checklist")
    @classmethod
    def _unique(cls, items: list[ChecklistEntry]) -> list[ChecklistEntry]:
        return unique_labels(items)


class FilingPatch(BaseModel):
    name: Name | None = None
    description: Text | None = None
    default_checklist: list[ChecklistEntry] | None = Field(default=None, max_length=MAX_CHECKLIST)
    is_active: bool | None = None

    @field_validator("default_checklist")
    @classmethod
    def _unique(cls, items: list[ChecklistEntry] | None) -> list[ChecklistEntry] | None:
        return None if items is None else unique_labels(items)


class FilingOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    default_checklist: list[ChecklistEntry]
    is_active: bool
    current: CalendarOut | None  # the newest published calendar: what new cycles will use
    draft: CalendarOut | None


class FilingDetailOut(FilingOut):
    versions: list[CalendarOut]  # newest first


class PreviewRow(BaseModel):
    key: str
    label: str
    period_start: date
    period_end: date
    due_date: date
    due_moved_from: date | None  # set when the holiday rule moved it
    alert_date: date
    alert_moved_from: date | None


class PreviewOut(BaseModel):
    year: int
    year_label: str
    lead_days: int
    lead_days_is_default: bool
    rows: list[PreviewRow]


def calendar_out(cv: CalendarVersion) -> CalendarOut:
    return CalendarOut(
        id=cv.id,
        version=cv.version,
        status=status_of(cv.published_at),
        frequency=cv.frequency,
        period_basis=cv.period_basis,
        due=DueRule(
            months_after=cv.due_months_after, day=cv.due_day, overrides=cv.due_overrides or {}
        ),
        lead_days=cv.lead_days,
        holiday_rule=cv.holiday_rule,
        notes=cv.notes,
        created_at=cv.created_at,
        created_by_id=cv.created_by_id,
        published_at=cv.published_at,
        published_by_id=cv.published_by_id,
    )


def _versions(db: Session, filing_ids: list[int]) -> dict[int, list[CalendarVersion]]:
    found: dict[int, list[CalendarVersion]] = {i: [] for i in filing_ids}
    if not filing_ids:
        return found
    rows = db.scalars(
        select(CalendarVersion)
        .where(CalendarVersion.filing_id.in_(filing_ids))
        .order_by(CalendarVersion.filing_id, CalendarVersion.version.desc())
    )
    for cv in rows:
        found[cv.filing_id].append(cv)
    return found


def current_calendar(versions: list[CalendarVersion]) -> CalendarVersion | None:
    return next((v for v in versions if v.published_at is not None), None)


def _draft(versions: list[CalendarVersion]) -> CalendarVersion | None:
    return next((v for v in versions if v.published_at is None), None)


def _filing_out(filing: Filing, versions: list[CalendarVersion]) -> FilingOut:
    current, draft = current_calendar(versions), _draft(versions)
    return FilingOut(
        id=filing.id,
        code=filing.code,
        name=filing.name,
        description=filing.description,
        default_checklist=[ChecklistEntry(**i) for i in filing.default_checklist],
        is_active=filing.is_active,
        current=calendar_out(current) if current else None,
        draft=calendar_out(draft) if draft else None,
    )


def _detail(db: Session, filing: Filing) -> FilingDetailOut:
    versions = _versions(db, [filing.id])[filing.id]
    return FilingDetailOut(
        **_filing_out(filing, versions).model_dump(),
        versions=[calendar_out(v) for v in versions],
    )


def _locked(db: Session, filing_id: int) -> Filing:
    filing = db.scalar(
        select(Filing)
        .where(Filing.id == filing_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if filing is None:
        raise ApiHTTPException(404, "No such filing type.", code="not_found")
    return filing


def _checklist(items: list[ChecklistEntry]) -> list[dict[str, Any]]:
    return [i.model_dump() for i in items]


@router.get("")
def list_filings(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[FilingOut]:
    filings = list(db.scalars(select(Filing).order_by(Filing.code)))
    versions = _versions(db, [f.id for f in filings])
    return [_filing_out(f, versions[f.id]) for f in filings]


@router.post("", status_code=201)
def create_filing(
    body: FilingIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FilingDetailOut:
    filing = Filing(
        code=body.code,
        name=body.name,
        description=body.description or None,
        default_checklist=_checklist(body.default_checklist),
        created_by_id=actor.user_id,
    )
    db.add(filing)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiHTTPException(
            409, f"A filing type with code {body.code} already exists.", code="duplicate"
        ) from None
    audit(
        db,
        "filing.created",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("filing", filing.id),
        details={"code": filing.code},
    )
    db.commit()
    return _detail(db, filing)


@router.post("/preview")
def preview_calendar(
    body: CalendarIn,
    _: Principal = Depends(admin_only),  # noqa: B008
    year: int | None = Query(None, ge=2000, le=2100),
    db: Session = Depends(get_db),  # noqa: B008
) -> PreviewOut:
    """FR-PK-02: a full year of due and alert dates for a calendar, before it is saved."""
    rule = make_rule(
        frequency=body.frequency,
        period_basis=body.period_basis,
        months_after=body.due.months_after,
        day=body.due.day,
        overrides={k: v.model_dump() for k, v in body.due.overrides.items()},
        lead_days=body.lead_days,
        holiday_rule=body.holiday_rule,
        defaults=lead_defaults(db),
    )
    chosen = year if year is not None else current_year(body.period_basis)
    try:
        rows = schedule_year(db, rule, chosen)
    except NoWorkingDay as exc:
        raise ApiHTTPException(422, str(exc), code="no_working_day") from None
    label = (
        f"FY {chosen}-{(chosen + 1) % 100:02d}"
        if body.period_basis is PeriodBasis.FINANCIAL_YEAR
        else str(chosen)
    )
    return PreviewOut(
        year=chosen,
        year_label=label,
        lead_days=rule.lead_days,
        lead_days_is_default=body.lead_days is None,
        rows=[
            PreviewRow(
                key=r.period.key,
                label=r.period.label,
                period_start=r.period.start,
                period_end=r.period.end,
                due_date=r.due_date,
                due_moved_from=r.due_rule_date if r.due_rule_date != r.due_date else None,
                alert_date=r.alert_date,
                alert_moved_from=r.alert_rule_date if r.alert_rule_date != r.alert_date else None,
            )
            for r in rows
        ],
    )


@router.get("/{filing_id}")
def get_filing(
    filing_id: int,
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FilingDetailOut:
    filing = db.get(Filing, filing_id)
    if filing is None:
        raise ApiHTTPException(404, "No such filing type.", code="not_found")
    return _detail(db, filing)


@router.patch("/{filing_id}")
def update_filing(
    filing_id: int,
    body: FilingPatch,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> FilingDetailOut:
    """Name, description, checklist template and whether it can be added to packages. These do
    not change any package (packages keep their own copy of the checklist)."""
    filing = _locked(db, filing_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and body.name is None:
        raise ApiHTTPException(422, "A filing type needs a name.", code="validation_error")
    if "default_checklist" in changes:
        filing.default_checklist = _checklist(body.default_checklist or [])
    if body.name is not None:
        filing.name = body.name
    if "description" in changes:
        filing.description = body.description or None
    if body.is_active is not None:
        filing.is_active = body.is_active
    audit(
        db,
        "filing.updated",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("filing", filing.id),
        details={"changed": sorted(changes), "is_active": filing.is_active},
    )
    db.commit()
    return _detail(db, filing)


@router.put("/{filing_id}/calendar/draft")
def save_calendar_draft(
    filing_id: int,
    body: CalendarIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> CalendarOut:
    """Create or replace the draft. If the newest version is published, this starts a new one."""
    _locked(db, filing_id)
    draft = db.scalar(
        select(CalendarVersion).where(
            CalendarVersion.filing_id == filing_id, CalendarVersion.published_at.is_(None)
        )
    )
    if draft is None:
        draft = CalendarVersion(
            filing_id=filing_id,
            version=next_version(db, CalendarVersion, CalendarVersion.filing_id, filing_id),
            created_by_id=actor.user_id,
            created_at=utcnow(),
        )
        db.add(draft)
    draft.frequency = body.frequency
    draft.period_basis = body.period_basis
    draft.due_months_after = body.due.months_after
    draft.due_day = body.due.day
    draft.due_overrides = {k: v.model_dump() for k, v in sorted(body.due.overrides.items())}
    draft.lead_days = body.lead_days
    draft.holiday_rule = body.holiday_rule
    draft.notes = body.notes or None
    db.flush()
    audit(
        db,
        "calendar.draft_saved",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("filing", filing_id),
        details={"version": draft.version},
    )
    db.commit()
    return calendar_out(draft)


@router.delete("/{filing_id}/calendar/draft", status_code=204)
def discard_calendar_draft(
    filing_id: int,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    _locked(db, filing_id)
    draft = db.scalar(
        select(CalendarVersion).where(
            CalendarVersion.filing_id == filing_id, CalendarVersion.published_at.is_(None)
        )
    )
    if draft is None:
        raise ApiHTTPException(404, "There is no draft to discard.", code="no_draft")
    version = draft.version
    db.delete(draft)
    audit(
        db,
        "calendar.draft_discarded",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("filing", filing_id),
        details={"version": version},
    )
    db.commit()
    return Response(status_code=204)


@router.post("/{filing_id}/calendar/publish")
def publish_calendar(
    filing_id: int,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> CalendarOut:
    """Make the draft the calendar for cycles created from now on (FR-PK-05)."""
    _locked(db, filing_id)
    draft = db.scalar(
        select(CalendarVersion).where(
            CalendarVersion.filing_id == filing_id, CalendarVersion.published_at.is_(None)
        )
    )
    if draft is None:
        raise ApiHTTPException(409, "There is no draft to publish.", code="no_draft")
    draft.published_at = utcnow()
    draft.published_by_id = actor.user_id
    audit(
        db,
        "calendar.published",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("filing", filing_id),
        details={"version": draft.version, "frequency": draft.frequency},
    )
    db.commit()
    return calendar_out(draft)
