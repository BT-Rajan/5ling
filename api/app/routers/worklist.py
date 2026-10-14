"""The daily work list (BRD FR-IN-04): received, missing, due this week, overdue.

Staff see their own clients' cycles; the Administrator sees everyone's, or one owner's. The
count report uses the same definitions (app.worklist.buckets) and the same scope rules.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, get_db, require_roles
from app.cycles.service import ticket_code
from app.cycles.stages import STAGE_LABEL
from app.errors import ApiHTTPException
from app.files.checklist import items_for
from app.models import Client, Cycle, Filing, Role, Stage, Ticket, User
from app.timeutil import today_ist
from app.worklist.buckets import BUCKETS, Bucket, Scope, condition, counts, in_scope, week_of

router = APIRouter(tags=["work list"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
LIST_LIMIT = 100


class ClientRef(BaseModel):
    client_id: int
    legal_name: str


class PersonRef(BaseModel):
    id: int
    full_name: str


class Row(BaseModel):
    code: str
    client: ClientRef
    filing: str
    period_label: str
    due_date: date
    stage: Stage
    stage_label: str
    missing: list[str]  # for the "missing" list: exactly the checklist items not yet received


class ListOut(BaseModel):
    total: int
    items: list[Row]  # soonest due first, at most 100


class Counts(BaseModel):
    received: int
    missing: int
    due_this_week: int
    overdue: int


class WorklistOut(BaseModel):
    on: date
    week_from: date
    week_to: date
    owner: PersonRef | None  # None: every owner
    counts: Counts
    lists: dict[str, ListOut]


class CountsOut(BaseModel):
    on: date
    owner_id: int | None
    counts: Counts


def _scope(
    db: Session, principal: Principal, request: Request, owner_id: int | None, on: date | None
) -> tuple[Scope, User | None]:
    day = on or today_ist()
    if abs((day - today_ist()).days) > 366:
        raise ApiHTTPException(422, "Choose a date within a year of today.", code="bad_date")
    if principal.role is not Role.ADMINISTRATOR:
        if owner_id is not None and owner_id != principal.user_id:
            raise ApiHTTPException(403, "You can only see your own work list.", code="forbidden")
        owner_id = principal.user_id
    owner = db.get(User, owner_id) if owner_id is not None else None
    if owner_id is not None and owner is None:
        raise ApiHTTPException(404, "No such person.", code="not_found")
    grace = request.app.state.settings.waiting_grace_days
    return Scope(on=day, owner_id=owner_id, grace_days=grace), owner


@router.get("/api/worklist")
def worklist(
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    owner_id: int | None = Query(None, ge=1),
    on: date | None = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> WorklistOut:
    """FR-IN-04: one daily list. Staff get their own; the Administrator, all or one owner's."""
    scope, owner = _scope(db, principal, request, owner_id, on)
    lists: dict[str, ListOut] = {}
    totals = counts(db, scope)
    for bucket in BUCKETS:
        rows = db.execute(
            select(Cycle, Client, Ticket.number, Filing.code)
            .join(Client, Client.id == Cycle.client_pk)
            .join(Ticket, Ticket.id == Cycle.ticket_id)
            .join(Filing, Filing.id == Cycle.filing_id)
            .where(in_scope(scope), condition(bucket, scope))
            .order_by(Cycle.due_date, Ticket.number)
            .limit(LIST_LIMIT)
        ).all()
        lists[bucket] = ListOut(total=totals[bucket], items=[_row(db, bucket, *r) for r in rows])
    monday, sunday = week_of(scope.on)
    return WorklistOut(
        on=scope.on,
        week_from=monday,
        week_to=sunday,
        owner=PersonRef(id=owner.id, full_name=owner.full_name) if owner else None,
        counts=Counts(**totals),
        lists=lists,
    )


def _row(
    db: Session, bucket: Bucket, cycle: Cycle, client: Client, number: int, filing: str
) -> Row:
    missing = (
        [v.item.label for v in items_for(db, cycle.id) if v.status != "received"]
        if bucket == "missing"
        else []
    )
    return Row(
        code=ticket_code(number),
        client=ClientRef(client_id=client.client_id, legal_name=client.legal_name),
        filing=filing,
        period_label=cycle.period_label,
        due_date=cycle.due_date,
        stage=cycle.stage,
        stage_label=STAGE_LABEL[cycle.stage],
        missing=missing,
    )


@router.get("/api/reports/worklist-counts")
def worklist_counts(
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    owner_id: int | None = Query(None, ge=1),
    on: date | None = None,
    db: Session = Depends(get_db),  # noqa: B008
) -> CountsOut:
    """The same four counts, as a report (FR-IN-04: they must match the work list)."""
    scope, _ = _scope(db, principal, request, owner_id, on)
    return CountsOut(on=scope.on, owner_id=scope.owner_id, counts=Counts(**counts(db, scope)))
