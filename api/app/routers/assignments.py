"""Assigning a filing ticket's work (BRD FR-WF-01, FR-WF-02). The client's owner or the Administrator."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.assignments.service import AssignRefused, assign, can_be_assigned, withdraw
from app.auth.deps import Principal, get_db, require_roles
from app.errors import ApiHTTPException
from app.models import Assignment, Client, Cycle, EndReason, Role, Ticket, User
from app.routers.tickets import _load, can_edit
from app.timeutil import today_ist

router = APIRouter(tags=["assignments"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]


class AssignIn(BaseModel):
    assignee_id: int = Field(ge=1)
    work_due_date: date | None = None
    note: Note | None = None


class WithdrawIn(BaseModel):
    note: Note = Field(min_length=3)


class Person(BaseModel):
    id: int
    full_name: str
    role: Role


class AssignmentOut(BaseModel):
    id: int
    assignee: Person
    work_due_date: date | None
    note: str | None
    assigned_by: str
    assigned_at: datetime
    ended_at: datetime | None
    end_reason: EndReason | None
    end_note: str | None


class AssignmentsOut(BaseModel):
    current: AssignmentOut | None
    history: list[AssignmentOut]  # newest first, including the current one


def _people(db: Session, ids: set[int]) -> dict[int, User]:
    return {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids or {0})))}


def _out(a: Assignment, people: dict[int, User]) -> AssignmentOut:
    who = people[a.assignee_id]
    return AssignmentOut(
        id=a.id,
        assignee=Person(id=who.id, full_name=who.full_name, role=who.role),
        work_due_date=a.work_due_date,
        note=a.note,
        assigned_by=people[a.assigned_by_id].full_name,
        assigned_at=a.assigned_at,
        ended_at=a.ended_at,
        end_reason=a.end_reason,
        end_note=a.end_note,
    )


def _history(db: Session, cycle_id: int) -> AssignmentsOut:
    rows = list(
        db.scalars(
            select(Assignment).where(Assignment.cycle_id == cycle_id).order_by(Assignment.id.desc())
        )
    )
    people = _people(db, {r.assignee_id for r in rows} | {r.assigned_by_id for r in rows})
    items = [_out(r, people) for r in rows]
    current = next((o for r, o in zip(rows, items, strict=True) if r.ended_at is None), None)
    return AssignmentsOut(current=current, history=items)


def _owned_cycle(
    db: Session, principal: Principal, ref: str, *, lock: bool = False
) -> tuple[Ticket, Client, Cycle]:
    ticket, client = _load(db, principal, ref, lock=lock)
    if not can_edit(principal, client):
        raise ApiHTTPException(403, "Only the client's owner can assign this.", code="forbidden")
    cycle = db.scalar(select(Cycle).where(Cycle.ticket_id == ticket.id))
    if cycle is None:
        raise ApiHTTPException(
            409, "Task tickets are given to someone directly.", code="not_a_cycle"
        )
    return ticket, client, cycle


@router.get("/api/assignment-candidates")
def candidates(
    _: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[Person]:
    """Active staff, and consultants with a signed confidentiality agreement."""
    people = db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.full_name))
    return [
        Person(id=u.id, full_name=u.full_name, role=u.role) for u in people if can_be_assigned(u)
    ]


@router.get("/api/tickets/{ticket}/assignment")
def get_assignment(
    ticket: str,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AssignmentsOut:
    found, _ = _load(db, principal, ticket)
    cycle_id = db.scalar(select(Cycle.id).where(Cycle.ticket_id == found.id))
    if cycle_id is None:
        return AssignmentsOut(current=None, history=[])
    return _history(db, cycle_id)


@router.post("/api/tickets/{ticket}/assignment")
def make_assignment(
    ticket: str,
    body: AssignIn,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AssignmentsOut:
    """FR-WF-01: give the work to an in-house person or a consultant (replacing any current one)."""
    found, client, cycle = _owned_cycle(db, principal, ticket, lock=True)
    if body.work_due_date is not None and body.work_due_date < today_ist():
        raise ApiHTTPException(422, "The work due date has already passed.", code="bad_date")
    assignee = db.get(User, body.assignee_id)
    if assignee is None:
        raise ApiHTTPException(422, "No such person.", code="bad_assignee")
    try:
        assign(
            db,
            request.app.state.settings,
            cycle=cycle,
            ticket=found,
            client=client,
            assignee=assignee,
            work_due=body.work_due_date,
            note=body.note or None,
            by_id=principal.user_id,
            by_name=principal.full_name,
            by_role=principal.role.value,
        )
    except AssignRefused as exc:
        db.rollback()
        raise ApiHTTPException(exc.status, str(exc), code=exc.code) from None
    db.commit()
    return _history(db, cycle.id)


@router.post("/api/tickets/{ticket}/assignment/withdraw")
def withdraw_assignment(
    ticket: str,
    body: WithdrawIn,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> AssignmentsOut:
    """FR-WF-02: take the work back. The assignee's access ends at once."""
    found, client, cycle = _owned_cycle(db, principal, ticket, lock=True)
    try:
        withdraw(
            db,
            request.app.state.settings,
            cycle=cycle,
            ticket=found,
            client=client,
            note=body.note,
            by_id=principal.user_id,
            by_role=principal.role.value,
        )
    except AssignRefused as exc:
        db.rollback()
        raise ApiHTTPException(exc.status, str(exc), code=exc.code) from None
    db.commit()
    return _history(db, cycle.id)
