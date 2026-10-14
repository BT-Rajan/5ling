"""Tickets: filing cycles and their stages, and ad hoc tasks (BRD FR-CY-02, CR-02, CR-03).

Who sees a ticket: the Administrator, the client's owner, and, for a task, the person it is
assigned to (that task only, not the rest of the client). The owner and the Administrator edit;
the assignee can only mark the task done or reopen it. Consultants get the cycles assigned to
them in chunk C21.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.audit import audit
from app.auth.access import RecordAccess, authorize_record
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.service import (
    MoveRefused,
    create_ticket,
    emit_state_event,
    parse_ticket,
    ticket_code,
    transition,
)
from app.cycles.stages import MOVES, ORDER, STAGE_LABEL, moves_from
from app.errors import ApiHTTPException
from app.models import (
    FIRST_CLIENT_ID,
    LAST_CLIENT_ID,
    CalendarVersion,
    Client,
    Cycle,
    DueDateChange,
    DueDateExtension,
    EventKind,
    Filing,
    Package,
    PackageVersion,
    Role,
    Stage,
    StageChange,
    Task,
    TaskStatus,
    Ticket,
    TicketType,
    User,
)
from app.timeutil import utcnow

router = APIRouter(tags=["tickets"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)]
TicketRef = Annotated[str, Path(min_length=1, max_length=16)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


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


class ExtensionOut(BaseModel):
    old_due_date: date
    new_due_date: date
    old_alert_date: date
    new_alert_date: date
    reason: str
    applied_at: datetime
    applied_by: str | None


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
    extensions: list[ExtensionOut]  # government extensions that moved its dates, newest first


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


class PersonRef(BaseModel):
    id: int
    full_name: str


class TaskOut(BaseModel):
    description: str | None
    assignee: PersonRef | None
    due_date: date | None
    status: TaskStatus
    status_label: str
    completed_at: datetime | None


class TicketOut(BaseModel):
    code: str
    number: int
    type: TicketType
    title: str
    client: ClientRef
    created_at: datetime
    cycle: CycleOut | None
    task: TaskOut | None
    history: list[HistoryOut]
    can_edit: bool  # the owner or the Administrator; an assignee only changes the task's status


class TicketRow(BaseModel):
    code: str
    type: TicketType
    title: str
    stage: Stage | None
    stage_label: str | None
    due_date: date | None


TASK_LABEL: dict[TaskStatus, str] = {
    TaskStatus.OPEN: "Open",
    TaskStatus.DONE: "Done",
    TaskStatus.CANCELLED: "Cancelled",
}


class TransitionIn(BaseModel):
    action: str
    expected_stage: Stage  # the stage the person was looking at: refuses double moves
    note: Note | None = None


def _move_out(m: object) -> MoveOut:
    return MoveOut.model_validate(m, from_attributes=True)


def can_edit(principal: Principal, client: Client) -> bool:
    return principal.role is Role.ADMINISTRATOR or client.owner_id == principal.user_id


def _load(
    db: Session, principal: Principal, ref: str, *, lock: bool = False
) -> tuple[Ticket, Client]:
    """The ticket, if this person may see it (record-level check, NFR-SE-01)."""
    number = parse_ticket(ref)
    ticket = (
        db.scalar(select(Ticket).where(Ticket.number == number)) if number is not None else None
    )
    client = db.get(Client, ticket.client_pk) if ticket else None
    if ticket is None or client is None:
        authorize_record(principal, None, what="ticket")  # raises 404
        raise ApiHTTPException(404, "Not found.", code="not_found")
    if lock:
        db.execute(select(Cycle.id).where(Cycle.ticket_id == ticket.id).with_for_update())
        db.execute(select(Task.id).where(Task.ticket_id == ticket.id).with_for_update())
    if not can_edit(principal, client):
        assignee = db.scalar(select(Task.assignee_id).where(Task.ticket_id == ticket.id))
        if assignee is None or assignee != principal.user_id:
            authorize_record(principal, RecordAccess(owner_id=client.owner_id), what="ticket")
    return ticket, client


def _task_out(db: Session, task: Task) -> TaskOut:
    person = db.get(User, task.assignee_id) if task.assignee_id else None
    return TaskOut(
        description=task.description,
        assignee=PersonRef(id=person.id, full_name=person.full_name) if person else None,
        due_date=task.due_date,
        status=task.status,
        status_label=TASK_LABEL[task.status],
        completed_at=task.completed_at,
    )


def _ticket_out(db: Session, ticket: Ticket, client: Client, principal: Principal) -> TicketOut:
    task = db.scalar(select(Task).where(Task.ticket_id == ticket.id))
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
            extensions=[
                ExtensionOut(
                    old_due_date=c.old_due_date,
                    new_due_date=c.new_due_date,
                    old_alert_date=c.old_alert_date,
                    new_alert_date=c.new_alert_date,
                    reason=e.reason,
                    applied_at=e.created_at,
                    applied_by=name,
                )
                for c, e, name in db.execute(
                    select(DueDateChange, DueDateExtension, User.full_name)
                    .join(DueDateExtension, DueDateExtension.id == DueDateChange.extension_id)
                    .outerjoin(User, User.id == DueDateExtension.created_by_id)
                    .where(DueDateChange.cycle_id == cycle.id)
                    .order_by(DueDateChange.id.desc())
                )
            ],
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
        task=_task_out(db, task) if task else None,
        history=history,
        can_edit=can_edit(principal, client),
    )


@router.get("/api/stages")
def stages(_: Principal = Depends(staff_or_admin)) -> StagesOut:  # noqa: B008
    """LL-042: the stages in order and every move allowed between them."""
    return StagesOut(
        stages=[StageOut(key=s, label=STAGE_LABEL[s]) for s in ORDER],
        moves=[_move_out(m) for m in MOVES],
    )


class TaskIn(BaseModel):
    client_id: int = Field(ge=FIRST_CLIENT_ID, le=LAST_CLIENT_ID)
    title: Title
    description: LongText | None = None
    assignee_id: int | None = Field(default=None, ge=1)
    due_date: date | None = None


class TaskPatch(BaseModel):
    title: Title | None = None
    description: LongText | None = None
    assignee_id: int | None = Field(default=None, ge=1)
    due_date: date | None = None
    status: TaskStatus | None = None
    expected_status: TaskStatus | None = None  # refuses a double click


class ListRow(BaseModel):
    code: str
    type: TicketType
    title: str
    client: ClientRef
    owner: PersonRef
    assignee: PersonRef | None
    state: str  # the cycle stage or the task status
    state_label: str
    due_date: date | None


class ListOut(BaseModel):
    items: list[ListRow]
    total: int
    offset: int
    limit: int


def _assignee(db: Session, assignee_id: int | None) -> User | None:
    if assignee_id is None:
        return None
    person = db.get(User, assignee_id)
    if person is None or not person.is_active or person.role is Role.CONSULTANT:
        raise ApiHTTPException(
            422, "Tasks go to an active Staff member or Administrator.", code="bad_assignee"
        )
    return person


def _sane_date(value: date | None) -> None:
    if value is not None and not 2000 <= value.year <= 2100:
        raise ApiHTTPException(422, "Choose a date between 2000 and 2100.", code="bad_date")


@router.get("/api/tickets/assignees")
def assignees(
    _: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonRef]:
    """Who a task can be given to: active Staff and Administrators."""
    people = db.scalars(
        select(User)
        .where(User.is_active.is_(True), User.role.in_([Role.STAFF, Role.ADMINISTRATOR]))
        .order_by(User.full_name)
    )
    return [PersonRef(id=u.id, full_name=u.full_name) for u in people]


@router.get("/api/tickets")
def list_tickets(
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    type: TicketType | None = None,
    state: str | None = Query(None, max_length=40),
    owner_id: int | None = Query(None, ge=1),
    assignee_id: int | None = Query(None, ge=1),
    client_id: int | None = Query(None, ge=FIRST_CLIENT_ID, le=LAST_CLIENT_ID),
    q: str | None = Query(None, max_length=100),
    open_only: bool = False,
    offset: int = Query(0, ge=0, le=100_000),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> ListOut:
    """LL-051: filings and tasks together, soonest due first. Staff see tickets of their own
    clients and tasks assigned to them."""
    Owner = aliased(User)
    Assignee = aliased(User)
    due = func.coalesce(Cycle.due_date, Task.due_date)
    stmt = (
        select(Ticket, Client, Cycle, Task, Owner.full_name, Assignee.id, Assignee.full_name)
        .join(Client, Client.id == Ticket.client_pk)
        .join(Owner, Owner.id == Client.owner_id)
        .outerjoin(Cycle, Cycle.ticket_id == Ticket.id)
        .outerjoin(Task, Task.ticket_id == Ticket.id)
        .outerjoin(Assignee, Assignee.id == Task.assignee_id)
    )
    if principal.role is not Role.ADMINISTRATOR:
        stmt = stmt.where(
            or_(Client.owner_id == principal.user_id, Task.assignee_id == principal.user_id)
        )
    if type is not None:
        stmt = stmt.where(Ticket.type == type)
    if state:
        stmt = stmt.where(or_(Cycle.stage == state, Task.status == state))
    if owner_id is not None:
        stmt = stmt.where(Client.owner_id == owner_id)
    if assignee_id is not None:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    if client_id is not None:
        stmt = stmt.where(Client.client_id == client_id)
    if open_only:
        stmt = stmt.where(or_(Cycle.stage != Stage.CLOSED, Task.status == TaskStatus.OPEN))
    text_query = " ".join((q or "").split())
    if text_query:
        number = parse_ticket(text_query)
        pattern = (
            "%" + text_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        if number is not None and text_query.upper().startswith("TKT"):
            stmt = stmt.where(Ticket.number == number)  # a ticket ID, and only that
        else:
            matches: list[ColumnElement[bool]] = [Ticket.title.like(pattern, escape="\\")]
            if number is not None:
                matches.append(Ticket.number == number)
            stmt = stmt.where(or_(*matches))
    total = int(db.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
    rows = db.execute(
        stmt.order_by(due.is_(None), due, Ticket.number).offset(offset).limit(limit)
    ).all()
    items = []
    for ticket, client, cycle, task, owner_name, assignee_pk, assignee_name in rows:
        if cycle is not None:
            state_key, label = cycle.stage.value, STAGE_LABEL[cycle.stage]
        elif task is not None:
            state_key, label = task.status.value, TASK_LABEL[task.status]
        else:
            state_key, label = "", ""
        items.append(
            ListRow(
                code=ticket_code(ticket.number),
                type=ticket.type,
                title=ticket.title,
                client=ClientRef(client_id=client.client_id, legal_name=client.legal_name),
                owner=PersonRef(id=client.owner_id, full_name=owner_name),
                assignee=PersonRef(id=assignee_pk, full_name=assignee_name)
                if assignee_pk
                else None,
                state=state_key,
                state_label=label,
                due_date=cycle.due_date if cycle else task.due_date if task else None,
            )
        )
    return ListOut(items=items, total=total, offset=offset, limit=limit)


@router.post("/api/tickets", status_code=201)
def create_task(
    body: TaskIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketOut:
    """LL-050: an ad hoc task on a client, with a TKT ID (CR-02). The owner or the Administrator."""
    client = db.scalar(select(Client).where(Client.client_id == body.client_id))
    authorize_record(
        principal, RecordAccess(owner_id=client.owner_id) if client else None, what="client"
    )
    if client is None:
        raise ApiHTTPException(404, "Not found.", code="not_found")
    person = _assignee(db, body.assignee_id)
    _sane_date(body.due_date)
    ticket = create_ticket(
        db,
        client=client,
        kind=TicketType.TASK,
        title=" ".join(body.title.split()),
        created_by_id=principal.user_id,
    )
    db.add(
        Task(
            ticket_id=ticket.id,
            description=body.description or None,
            assignee_id=person.id if person else None,
            due_date=body.due_date,
            status=TaskStatus.OPEN,
        )
    )
    audit(
        db,
        "task.created",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("ticket", ticket_code(ticket.number)),
        details={
            "client_id": client.client_id,
            "assignee_id": person.id if person else None,
            "due_date": body.due_date.isoformat() if body.due_date else None,
        },
    )
    db.commit()
    return _ticket_out(db, ticket, client, principal)


@router.patch("/api/tickets/{ticket}")
def update_task(
    ticket: TicketRef,
    body: TaskPatch,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketOut:
    """Change a task. The assignee may only change its status (done, or open again)."""
    found, client = _load(db, principal, ticket, lock=True)
    task = db.scalar(select(Task).where(Task.ticket_id == found.id))
    if task is None:
        raise ApiHTTPException(
            409, "Filing tickets move by stage, not by editing.", code="not_a_task"
        )
    changes = body.model_dump(exclude_unset=True)
    changes.pop("expected_status", None)
    if not can_edit(principal, client) and set(changes) - {"status"}:
        raise ApiHTTPException(
            403,
            "Only the client's owner or the Administrator can change this task.",
            code="forbidden",
        )
    if body.expected_status is not None and body.expected_status is not task.status:
        raise ApiHTTPException(
            409,
            f"This task is already {TASK_LABEL[task.status].lower()}. Refresh to see it.",
            code="status_changed",
        )
    if "title" in changes:
        if body.title is None:
            raise ApiHTTPException(422, "A task needs a title.", code="validation_error")
        found.title = " ".join(body.title.split())
    if "description" in changes:
        task.description = body.description or None
    if "assignee_id" in changes:
        task.assignee_id = person.id if (person := _assignee(db, body.assignee_id)) else None
    if "due_date" in changes:
        _sane_date(body.due_date)
        task.due_date = body.due_date
    if body.status is not None and body.status is not task.status:
        emit_state_event(
            db,
            EventKind.TASK_STATUS,
            client_pk=client.id,
            ticket_id=found.id,
            from_state=task.status.value,
            to_state=body.status.value,
        )
        task.status = body.status
        task.completed_at = utcnow() if body.status is TaskStatus.DONE else None
    if not changes:
        raise ApiHTTPException(409, "Nothing would change.", code="no_change")
    task.updated_at = utcnow()
    audit(
        db,
        "task.updated",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("ticket", ticket_code(found.number)),
        details={"changed": sorted(changes), "status": task.status.value},
    )
    db.commit()
    return _ticket_out(db, found, client, principal)


@router.get("/api/tickets/{ticket}")
def get_ticket(
    ticket: TicketRef,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketOut:
    found, client = _load(db, principal, ticket)
    return _ticket_out(db, found, client, principal)


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
    return _ticket_out(db, found, client, principal)


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
