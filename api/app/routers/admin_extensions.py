"""Government due-date extensions (BRD FR-PK-06). Administrator only.

Preview first, then apply with a reason. Applying names how many cycles the preview showed; if
that has changed since, nothing is applied and the Administrator previews again.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.extensions import ExtensionRefused, apply, open_periods, plan
from app.cycles.service import ticket_code
from app.cycles.stages import STAGE_LABEL
from app.errors import ApiHTTPException
from app.models import DueDateExtension, Filing, Role, Stage, User
from app.schedule.engine import NoWorkingDay

router = APIRouter(prefix="/api/admin/extensions", tags=["admin: due-date extensions"])

admin_only = require_roles(Role.ADMINISTRATOR)

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
PREVIEW_ROWS = 500  # rows listed in a preview; the counts are always exact


class ExtensionIn(BaseModel):
    filing_id: int = Field(ge=1)
    period_start: date
    new_due_date: date


class ApplyIn(ExtensionIn):
    reason: Reason
    expected_changes: int = Field(ge=0, le=1_000_000)  # how many cycles the preview showed


class PeriodOut(BaseModel):
    period_start: date
    period_label: str
    open_cycles: int
    earliest_due: date
    latest_due: date


class AffectedOut(BaseModel):
    code: str
    client_id: int
    client_name: str
    stage: Stage
    stage_label: str
    old_due_date: date
    new_due_date: date
    old_alert_date: date
    new_alert_date: date


class PreviewOut(BaseModel):
    filing_code: str
    period_label: str
    new_due_date: date
    changes: int
    unchanged: int  # open cycles already due on or after the new date
    rows: list[AffectedOut]  # the first PREVIEW_ROWS of them


class ExtensionOut(BaseModel):
    id: int
    filing_code: str
    filing_name: str
    period_start: date
    period_label: str
    new_due_date: date
    reason: str
    cycles_changed: int
    created_by: str | None
    created_at: datetime


def _filing(db: Session, filing_id: int) -> Filing:
    filing = db.get(Filing, filing_id)
    if filing is None:
        raise ApiHTTPException(404, "No such filing type.", code="not_found")
    return filing


def _refused(exc: ExtensionRefused) -> ApiHTTPException:
    return ApiHTTPException(exc.status, str(exc), code=exc.code)


@router.get("")
def list_extensions(
    _: Principal = Depends(admin_only),  # noqa: B008
    filing_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ExtensionOut]:
    """Extensions applied so far, newest first."""
    stmt = (
        select(DueDateExtension, Filing.code, Filing.name, User.full_name)
        .join(Filing, Filing.id == DueDateExtension.filing_id)
        .outerjoin(User, User.id == DueDateExtension.created_by_id)
        .order_by(DueDateExtension.id.desc())
        .limit(200)
    )
    if filing_id is not None:
        stmt = stmt.where(DueDateExtension.filing_id == filing_id)
    return [
        ExtensionOut(
            id=e.id,
            filing_code=code,
            filing_name=name,
            period_start=e.period_start,
            period_label=e.period_label,
            new_due_date=e.new_due_date,
            reason=e.reason,
            cycles_changed=e.cycles_changed,
            created_by=who,
            created_at=e.created_at,
        )
        for e, code, name, who in db.execute(stmt)
    ]


@router.get("/periods")
def periods(
    _: Principal = Depends(admin_only),  # noqa: B008
    filing_id: int = Query(ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PeriodOut]:
    """The periods of a filing that still have open cycles: the ones an extension can move."""
    _filing(db, filing_id)
    return [
        PeriodOut(
            period_start=start,
            period_label=label,
            open_cycles=n,
            earliest_due=lo,
            latest_due=hi,
        )
        for start, label, n, lo, hi in open_periods(db, filing_id)
    ]


@router.post("/preview")
def preview(
    body: ExtensionIn,
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PreviewOut:
    """FR-PK-06: the open cycles the extension would move, with their new alert dates."""
    filing = _filing(db, body.filing_id)
    try:
        planned = plan(db, filing.id, body.period_start, body.new_due_date)
    except ExtensionRefused as exc:
        raise _refused(exc) from None
    except NoWorkingDay as exc:
        raise ApiHTTPException(422, str(exc), code="no_working_day") from None
    return PreviewOut(
        filing_code=filing.code,
        period_label=planned.period_label,
        new_due_date=body.new_due_date,
        changes=len(planned.changes),
        unchanged=planned.unchanged,
        rows=[
            AffectedOut(
                code=ticket_code(p.ticket_number),
                client_id=p.client_id,
                client_name=p.client_name,
                stage=p.cycle.stage,
                stage_label=STAGE_LABEL[p.cycle.stage],
                old_due_date=p.cycle.due_date,
                new_due_date=p.new_due_date,
                old_alert_date=p.cycle.alert_date,
                new_alert_date=p.new_alert_date,
            )
            for p in planned.changes[:PREVIEW_ROWS]
        ],
    )


@router.post("", status_code=201)
def apply_extension(
    body: ApplyIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ExtensionOut:
    """FR-PK-06: move every affected open cycle to the new date in one action, with a reason."""
    filing = _filing(db, body.filing_id)
    try:
        extension, planned = apply(
            db,
            filing=filing,
            period_start=body.period_start,
            new_due=body.new_due_date,
            reason=body.reason,
            expected_changes=body.expected_changes,
            actor_id=actor.user_id,
        )
    except ExtensionRefused as exc:
        db.rollback()
        raise _refused(exc) from None
    except NoWorkingDay as exc:
        db.rollback()
        raise ApiHTTPException(422, str(exc), code="no_working_day") from None
    audit(
        db,
        "calendar.due_date_extended",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("filing", filing.id),
        details={
            "extension": extension.id,
            "filing": filing.code,
            "period": planned.period_label,
            "period_start": body.period_start.isoformat(),
            "new_due_date": body.new_due_date.isoformat(),
            "reason": body.reason,
            "cycles_changed": len(planned.changes),
            "unchanged": planned.unchanged,
        },
    )
    db.commit()
    who = db.get(User, actor.user_id)
    return ExtensionOut(
        id=extension.id,
        filing_code=filing.code,
        filing_name=filing.name,
        period_start=extension.period_start,
        period_label=extension.period_label,
        new_due_date=extension.new_due_date,
        reason=extension.reason,
        cycles_changed=extension.cycles_changed,
        created_by=who.full_name if who else None,
        created_at=extension.created_at,
    )
