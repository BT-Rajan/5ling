"""Tickets and their stages (BRD FR-CY-02, CR-02). Administrator, or the client's owner.

Consultants get access to the cycles assigned to them in chunk C21.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.access import RecordAccess, authorize_record
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.service import MoveRefused, parse_ticket, ticket_code, transition
from app.cycles.stages import MOVES, ORDER, STAGE_LABEL, moves_from
from app.errors import ApiHTTPException
from app.models import (
    FIRST_CLIENT_ID,
    LAST_CLIENT_ID,
    CalendarVersion,
    Client,
    Cycle,
    Filing,
    Package,
    PackageVersion,
    Role,
    Stage,
    StageChange,
    Ticket,
    TicketType,
    User,
)

router = APIRouter(tags=["tickets"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
TicketRef = Annotated[str, Path(min_length=1, max_length=16)]


class StageOut(BaseModel):
    key: Stage
    label: str


class MoveOut(BaseModel):
    action: str
    label: str
    source: Stage
    target: Stage
    needs_note: bool


class StagesOut(BaseModel):
    stages: list[StageOut]
    moves: list[MoveOut]


class NamedRef(BaseModel):
    code: str
    name: str


class CycleOut(BaseModel):
    filing: NamedRef
    period_key: str
    period_label: str
    period_start: date
    period_end: date
    due_date: date
    alert_date: date
    stage: Stage
    stage_label: str
    stage_changed_at: datetime
    package_name: str
    package_version: int
    calendar_version: int
    moves: list[MoveOut]  # what can be done next


class HistoryOut(BaseModel):
    from_stage: Stage | None
    to_stage: Stage
    action: str
    note: str | None
    changed_by: str | None  # None: the system
    changed_at: datetime


class ClientRef(BaseModel):
    client_id: int
    legal_name: str


class TicketOut(BaseModel):
    code: str
    number: int
    type: TicketType
    title: str
    client: ClientRef
    created_at: datetime
    cycle: CycleOut | None
    history: list[HistoryOut]


class TicketRow(BaseModel):
    code: str
    type: TicketType
    title: str
    stage: Stage | None
    stage_label: str | None
    due_date: date | None


class TransitionIn(BaseModel):
    action: str
    expected_stage: Stage  # the stage the person was looking at: refuses double moves
    note: Note | None = None


def _move_out(m: object) -> MoveOut:
    return MoveOut.model_validate(m, from_attributes=True)


def _load(
    db: Session, principal: Principal, ref: str, *, lock: bool = False
) -> tuple[Ticket, Client]:
    number = parse_ticket(ref)
    ticket = (
        db.scalar(select(Ticket).where(Ticket.number == number)) if number is not None else None
    )
    client = db.get(Client, ticket.client_pk) if ticket else None
    if ticket is None or client is None:
        authorize_record(principal, None, what="ticket")  # raises 404
        raise ApiHTTPException(404, "Not found.", code="not_found")
    authorize_record(principal, RecordAccess(owner_id=client.owner_id), what="ticket")
    if lock:
        db.execute(select(Cycle.id).where(Cycle.ticket_id == ticket.id).with_for_update())
    return ticket, client


def _ticket_out(db: Session, ticket: Ticket, client: Client) -> TicketOut:
    cycle = db.scalar(select(Cycle).where(Cycle.ticket_id == ticket.id))
    cycle_out: CycleOut | None = None
    history: list[HistoryOut] = []
    if cycle is not None:
        filing = db.get(Filing, cycle.filing_id)
        version = db.get(PackageVersion, cycle.package_version_id)
        package = db.get(Package, version.package_id) if version else None
        calendar = db.get(CalendarVersion, cycle.calendar_version_id)
        if filing is None or version is None or package is None or calendar is None:
            raise RuntimeError("cycle refers to missing catalog rows")  # foreign keys prevent it
        cycle_out = CycleOut(
            filing=NamedRef(code=filing.code, name=filing.name),
            period_key=cycle.period_key,
            period_label=cycle.period_label,
            period_start=cycle.period_start,
            period_end=cycle.period_end,
            due_date=cycle.due_date,
            alert_date=cycle.alert_date,
            stage=cycle.stage,
            stage_label=STAGE_LABEL[cycle.stage],
            stage_changed_at=cycle.stage_changed_at,
            package_name=package.name,
            package_version=version.version,
            calendar_version=calendar.version,
            moves=[_move_out(m) for m in moves_from(cycle.stage)],
        )
        rows = db.execute(
            select(StageChange, User.full_name)
            .outerjoin(User, User.id == StageChange.changed_by_id)
            .where(StageChange.cycle_id == cycle.id)
            .order_by(StageChange.id.desc())
        ).all()
        history = [
            HistoryOut(
                from_stage=c.from_stage,
                to_stage=c.to_stage,
                action=c.action,
                note=c.note,
                changed_by=name,
                changed_at=c.changed_at,
            )
            for c, name in rows
        ]
    return TicketOut(
        code=ticket_code(ticket.number),
        number=ticket.number,
        type=ticket.type,
        title=ticket.title,
        client=ClientRef(client_id=client.client_id, legal_name=client.legal_name),
        created_at=ticket.created_at,
        cycle=cycle_out,
        history=history,
    )


@router.get("/api/stages")
def stages(_: Principal = Depends(staff_or_admin)) -> StagesOut:  # noqa: B008
    """LL-042: the stages in order and every move allowed between them."""
    return StagesOut(
        stages=[StageOut(key=s, label=STAGE_LABEL[s]) for s in ORDER],
        moves=[_move_out(m) for m in MOVES],
    )


@router.get("/api/tickets/{ticket}")
def get_ticket(
    ticket: TicketRef,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketOut:
    found, client = _load(db, principal, ticket)
    return _ticket_out(db, found, client)


@router.post("/api/tickets/{ticket}/transition")
def move_ticket(
    ticket: TicketRef,
    body: TransitionIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketOut:
    """Move a cycle to its next stage. The server refuses skips and any move not in the table."""
    found, client = _load(db, principal, ticket, lock=True)
    cycle = db.scalar(select(Cycle).where(Cycle.ticket_id == found.id))
    if cycle is None:
        raise ApiHTTPException(409, "This ticket has no filing stages.", code="not_a_cycle")
    try:
        transition(
            db,
            cycle,
            found,
            body.action,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            expected_stage=body.expected_stage,
            note=body.note,
        )
    except MoveRefused as refused:
        db.rollback()
        raise ApiHTTPException(
            409,
            str(refused),
            code=refused.code,
            extra={"allowed": [m.action for m in refused.allowed]},
        ) from None
    db.commit()
    return _ticket_out(db, found, client)


@router.get("/api/clients/{client_id}/tickets")
def client_tickets(
    client_id: Annotated[int, Path(ge=FIRST_CLIENT_ID, le=LAST_CLIENT_ID)],
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[TicketRow]:
    client = db.scalar(select(Client).where(Client.client_id == client_id))
    authorize_record(
        principal, RecordAccess(owner_id=client.owner_id) if client else None, what="client"
    )
    if client is None:
        raise ApiHTTPException(404, "Not found.", code="not_found")
    rows = db.execute(
        select(Ticket, Cycle)
        .outerjoin(Cycle, Cycle.ticket_id == Ticket.id)
        .where(Ticket.client_pk == client.id)
        .order_by(Cycle.due_date.is_(None), Cycle.due_date, Ticket.number)
    ).all()
    return [
        TicketRow(
            code=ticket_code(t.number),
            type=t.type,
            title=t.title,
            stage=c.stage if c else None,
            stage_label=STAGE_LABEL[c.stage] if c else None,
            due_date=c.due_date if c else None,
        )
        for t, c in rows
    ]
