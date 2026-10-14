"""What a consultant can see and do (BRD FR-WF-02): their open assignments, those cycles'
documents, and a place to upload their work. Nothing else.

An assignment that has ended (withdrawn, given to someone else, or the cycle closed) answers
"no access" from the next request.

C22 (FR-WF-03, FR-WF-04): the consultant prepares the work. Their first upload moves the cycle
from Assigned to Preparing, and they send it for review when it is ready. Once it is with the
reviewer, uploads are refused, so what is approved is what was reviewed. A return comes back
with the reviewer's comment, which the consultant sees here. Any other record is refused as "not found" and the attempt
is logged, so a consultant learns nothing about work that is not theirs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.auth.events import security_event
from app.cycles.service import MoveRefused, ticket_code, transition
from app.cycles.stages import BY_ACTION, STAGE_LABEL
from app.errors import ApiHTTPException
from app.files.documents import Infected, accept, forget_file
from app.files.types import ALLOWED_TEXT, FileRefused
from app.mail.updates import draft_updates
from app.models import (
    Assignment,
    Client,
    Cycle,
    Document,
    DocumentVersion,
    Filing,
    Role,
    Stage,
    StageChange,
    Ticket,
    UploadedVia,
    User,
)
from app.routers.documents import DocumentOut, documents_of

router = APIRouter(tags=["consultant"])

consultant_only = require_roles(Role.CONSULTANT)

# The stages in which the work is with the preparer, so they may upload.
WORKING = (Stage.ASSIGNED, Stage.PREPARING)
# Stage changes a consultant is shown: the review conversation, nothing else.
REVIEW_ACTIONS = ("submit_for_review", "return", "approve")


class WorkOut(BaseModel):
    id: int
    ticket: str
    client_name: str
    filing: str
    period_label: str
    due_date: date
    work_due_date: date | None
    note: str | None
    stage_label: str
    assigned_by: str
    assigned_at: datetime


class ReviewNote(BaseModel):
    action: str
    label: str
    note: str | None
    by: str | None
    at: datetime


class WorkDetail(WorkOut):
    documents: list[DocumentOut]
    allowed: str
    max_mb: int
    can_upload: bool
    can_submit: bool
    review: list[ReviewNote]


class SubmitIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


def _work_out(db: Session, a: Assignment) -> WorkOut:
    cycle = db.get(Cycle, a.cycle_id)
    ticket = db.get(Ticket, cycle.ticket_id) if cycle else None
    client = db.get(Client, cycle.client_pk) if cycle else None
    filing = db.get(Filing, cycle.filing_id) if cycle else None
    by = db.get(User, a.assigned_by_id)
    if cycle is None or ticket is None or client is None or filing is None or by is None:
        raise RuntimeError("assignment refers to missing rows")  # foreign keys prevent it
    return WorkOut(
        id=a.id,
        ticket=ticket_code(ticket.number),
        client_name=client.legal_name,
        filing=filing.code,
        period_label=cycle.period_label,
        due_date=cycle.due_date,
        work_due_date=a.work_due_date,
        note=a.note,
        stage_label=STAGE_LABEL[cycle.stage],
        assigned_by=by.full_name,
        assigned_at=a.assigned_at,
    )


def _mine(db: Session, principal: Principal, assignment_id: int) -> tuple[Assignment, Cycle]:
    a = db.get(Assignment, assignment_id)
    if a is None or a.assignee_id != principal.user_id:
        security_event(
            "access.denied",
            user_id=principal.user_id,
            role=principal.role.value,
            record="assignment",
        )
        raise ApiHTTPException(404, "Not found.", code="not_found")
    cycle = db.get(Cycle, a.cycle_id)
    if a.ended_at is not None or cycle is None or cycle.stage is Stage.CLOSED:
        security_event(
            "access.denied",
            user_id=principal.user_id,
            role=principal.role.value,
            record="assignment",
            reason="ended",
        )
        raise ApiHTTPException(
            403, "This work has ended, so you no longer have access to it.", code="no_access"
        )
    return a, cycle


@router.get("/api/consultant/assignments")
def my_work(
    principal: Principal = Depends(consultant_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[WorkOut]:
    rows = db.scalars(
        select(Assignment)
        .join(Cycle, Cycle.id == Assignment.cycle_id)
        .where(
            Assignment.assignee_id == principal.user_id,
            Assignment.ended_at.is_(None),
            Cycle.stage != Stage.CLOSED,
        )
        .order_by(Assignment.work_due_date.is_(None), Assignment.work_due_date, Cycle.due_date)
    )
    return [_work_out(db, a) for a in rows]


@router.get("/api/consultant/assignments/{assignment_id}")
def work_detail(
    assignment_id: int,
    request: Request,
    principal: Principal = Depends(consultant_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> WorkDetail:
    a, cycle = _mine(db, principal, assignment_id)
    return _detail(db, request, principal, a, cycle)


def _uploaded_by(db: Session, cycle_id: int, user_id: int) -> bool:
    return (
        db.scalar(
            select(DocumentVersion.id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.cycle_id == cycle_id, DocumentVersion.uploaded_by_id == user_id)
            .limit(1)
        )
        is not None
    )


def _detail(
    db: Session, request: Request, principal: Principal, a: Assignment, cycle: Cycle
) -> WorkDetail:
    settings = request.app.state.settings
    rows = db.execute(
        select(StageChange, User.full_name)
        .outerjoin(User, User.id == StageChange.changed_by_id)
        .where(StageChange.cycle_id == cycle.id, StageChange.action.in_(REVIEW_ACTIONS))
        .order_by(StageChange.changed_at, StageChange.id)
    ).all()
    return WorkDetail(
        **_work_out(db, a).model_dump(),
        documents=documents_of(db, cycle.id),
        allowed=ALLOWED_TEXT,
        max_mb=settings.max_upload_bytes // (1024 * 1024),
        can_upload=cycle.stage in WORKING,
        can_submit=cycle.stage is Stage.PREPARING and _uploaded_by(db, cycle.id, principal.user_id),
        review=[
            ReviewNote(
                action=sc.action,
                label=BY_ACTION[sc.action].label,
                note=sc.note,
                by=name,
                at=sc.changed_at,
            )
            for sc, name in rows
        ],
    )


def _locked(db: Session, principal: Principal, assignment_id: int) -> tuple[Assignment, Cycle]:
    """The assignment and its cycle, locked, re-checked under the lock."""
    a, cycle = _mine(db, principal, assignment_id)
    db.scalar(
        select(Cycle)
        .where(Cycle.id == cycle.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    db.scalar(
        select(Assignment)
        .where(Assignment.id == a.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return _mine(db, principal, assignment_id)


def _move(
    db: Session, principal: Principal, cycle: Cycle, action: str, note: str | None = None
) -> None:
    ticket = db.get(Ticket, cycle.ticket_id)
    if ticket is None:  # a foreign key prevents it
        raise RuntimeError("cycle without its ticket")
    try:
        transition(
            db,
            cycle,
            ticket,
            action,
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            expected_stage=BY_ACTION[action].source,
            note=note,
        )
    except MoveRefused as refused:
        db.rollback()
        raise ApiHTTPException(409, str(refused), code=refused.code) from None


@router.post("/api/consultant/assignments/{assignment_id}/submit")
def submit_for_review(
    assignment_id: int,
    body: SubmitIn,
    request: Request,
    principal: Principal = Depends(consultant_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> WorkDetail:
    """FR-WF-03: send the prepared work for review. It must include work they uploaded."""
    a, cycle = _locked(db, principal, assignment_id)
    if cycle.stage is not Stage.PREPARING:
        raise ApiHTTPException(
            409,
            f"This is in {STAGE_LABEL[cycle.stage]}, so it cannot be sent for review now.",
            code="illegal_transition",
        )
    if not _uploaded_by(db, cycle.id, principal.user_id):
        raise ApiHTTPException(
            409, "Upload your work before sending it for review.", code="no_work"
        )
    _move(db, principal, cycle, "submit_for_review", body.note)
    client = db.get(Client, cycle.client_pk)
    if client is not None:
        draft_updates(db, request.app.state.settings.firm_name, client_pk=client.id)  # CR-03
    db.commit()
    return _detail(db, request, principal, a, cycle)


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/api/uploads/consultant/assignments/{assignment_id}", status_code=201)
def upload_work(
    assignment_id: int,
    request: Request,
    x_file_name: Annotated[str, Header(max_length=600)],
    title: Annotated[str | None, Query(max_length=200)] = None,
    document_id: Annotated[int | None, Query(ge=1)] = None,
    body: bytes = Depends(raw_body),
    principal: Principal = Depends(consultant_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[DocumentOut]:
    """Upload work for the assigned cycle: a new document, or a new version of one."""
    settings = request.app.state.settings
    _, cycle = _locked(db, principal, assignment_id)
    if cycle.stage not in WORKING:
        raise ApiHTTPException(
            409,
            "This work is with the reviewer, so it cannot be changed now.",
            code="not_preparing",
        )
    client = db.get(Client, cycle.client_pk)
    if client is None:  # a foreign key prevents it
        raise RuntimeError("cycle without its client")
    store = request.app.state.store
    try:
        got = accept(
            db,
            store,
            request.app.state.scanner,
            client=client,
            cycle=cycle,
            name=unquote(x_file_name),
            data=body,
            max_bytes=settings.max_upload_bytes,
            title=title or f"Work from {principal.full_name}",
            document_id=document_id,
            via=UploadedVia.CONSULTANT,
            uploaded_by_id=principal.user_id,
        )
    except (FileRefused, Infected) as exc:
        db.rollback()
        infected = isinstance(exc, Infected)
        audit(
            db,
            "document.infected" if infected else "document.rejected",
            actor_user_id=principal.user_id,
            actor_role=principal.role.value,
            target=("cycle", cycle.id),
            details={
                "reason": (exc.signature or "malware") if isinstance(exc, Infected) else str(exc)
            },
        )
        db.commit()
        message = (
            "A virus was found in this file, so it was not accepted." if infected else str(exc)
        )
        raise ApiHTTPException(
            422, message, code="infected" if infected else "file_refused"
        ) from None
    audit(
        db,
        "document.uploaded",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("document_version", got.version.id),
        details={"via": "consultant", "assignment": assignment_id, "version": got.version.version},
    )
    if cycle.stage is Stage.ASSIGNED:
        _move(db, principal, cycle, "start_preparing")  # their first work starts the preparing
        draft_updates(db, settings.firm_name, client_pk=client.id)  # CR-03
    try:
        db.commit()
    except Exception:
        forget_file(store, got.version)
        raise
    return documents_of(db, cycle.id)
