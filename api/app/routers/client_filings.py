"""A client's own filings: add or remove one for that client only (BRD FR-PK-07, chunk C37).

Owners and Administrators see and change a client's filings (break-glass applies as on the client
page). A Staff member's removal waits for an Administrator, who sees the waiting list and decides.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.exceptions import (
    ChangeRefused,
    Outcome,
    decide,
    effective_filings,
    package_filings,
    request_change,
)
from app.cycles.generator import run_quietly
from app.errors import ApiHTTPException
from app.models import (
    CalendarVersion,
    Client,
    ClientFilingChange,
    Filing,
    FilingChangeKind,
    FilingChangeStatus,
    JobTrigger,
    Role,
    User,
)
from app.routers.clients import load_client

router = APIRouter(tags=["clients: filings"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
admin_only = require_roles(Role.ADMINISTRATOR)

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class FilingRef(BaseModel):
    id: int
    code: str
    name: str


class ClientFilingOut(FilingRef):
    from_package: bool  # False: added for this client alone
    removal_pending: bool


class ChangeOut(BaseModel):
    id: int
    client_id: int
    client_name: str
    filing: FilingRef
    kind: FilingChangeKind
    status: FilingChangeStatus
    reason: str
    requested_by: str
    requested_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    decision_note: str | None
    withdrawn: list[str] = []  # tickets of the unstarted cycles this removal closed


class ClientFilingsOut(BaseModel):
    filings: list[ClientFilingOut]
    available: list[FilingRef]  # filings that could be added
    changes: list[ChangeOut]  # newest first


class ChangeIn(BaseModel):
    filing_id: int = Field(ge=1)
    action: Literal["add", "remove"]
    reason: Reason


class DecisionIn(BaseModel):
    approve: bool
    note: Note | None = None


def _changes(db: Session, *conditions: object, limit: int = 200) -> list[ChangeOut]:
    asker, decider = aliased(User), aliased(User)
    rows = db.execute(
        select(ClientFilingChange, Client, Filing, asker.full_name, decider.full_name)
        .join(Client, Client.id == ClientFilingChange.client_pk)
        .join(Filing, Filing.id == ClientFilingChange.filing_id)
        .join(asker, asker.id == ClientFilingChange.requested_by_id)
        .outerjoin(decider, decider.id == ClientFilingChange.decided_by_id)
        .where(*conditions)  # type: ignore[arg-type]
        .order_by(ClientFilingChange.id.desc())
        .limit(limit)
    ).all()
    return [
        ChangeOut(
            id=c.id,
            client_id=client.client_id,
            client_name=client.legal_name,
            filing=FilingRef(id=f.id, code=f.code, name=f.name),
            kind=c.kind,
            status=c.status,
            reason=c.reason,
            requested_by=asked,
            requested_at=c.requested_at,
            decided_by=decided,
            decided_at=c.decided_at,
            decision_note=c.decision_note,
        )
        for c, client, f, asked, decided in rows
    ]


def _filings_out(db: Session, client: Client) -> ClientFilingsOut:
    current = effective_filings(db, client)
    in_package = set(package_filings(db, client))
    pending = set(
        db.scalars(
            select(ClientFilingChange.filing_id).where(
                ClientFilingChange.client_pk == client.id,
                ClientFilingChange.status == FilingChangeStatus.PENDING,
            )
        )
    )
    by_id = {f.id: f for f in db.scalars(select(Filing))}
    published = set(
        db.scalars(
            select(CalendarVersion.filing_id).where(CalendarVersion.published_at.is_not(None))
        )
    )
    return ClientFilingsOut(
        filings=[
            ClientFilingOut(
                id=i,
                code=by_id[i].code,
                name=by_id[i].name,
                from_package=i in in_package,
                removal_pending=i in pending,
            )
            for i in current
        ],
        available=[
            FilingRef(id=f.id, code=f.code, name=f.name)
            for f in sorted(by_id.values(), key=lambda f: f.code)
            if f.id not in current and f.is_active and f.id in published
        ],
        changes=_changes(db, ClientFilingChange.client_pk == client.id, limit=50),
    )


@router.get("/api/clients/{client_id}/filings")
def client_filings(
    client_id: Annotated[int, Path(ge=1)],
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ClientFilingsOut:
    client = load_client(db, principal, client_id)
    return _filings_out(db, client)


@router.post("/api/clients/{client_id}/filings", status_code=201)
def change_client_filings(
    client_id: Annotated[int, Path(ge=1)],
    body: ChangeIn,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ClientFilingsOut:
    """Add a filing (at once) or ask to remove one (an Administrator approves)."""
    client = load_client(db, principal, client_id, lock=True)  # the owner or an Administrator
    try:
        outcome = request_change(
            db,
            client,
            body.filing_id,
            FilingChangeKind(body.action),
            body.reason,
            user_id=principal.user_id,
            role=principal.role.value,
        )
    except ChangeRefused as exc:
        db.rollback()
        raise ApiHTTPException(exc.status, exc.message, code=exc.code) from None
    db.commit()
    if outcome.change.kind is FilingChangeKind.ADD:  # its cycles appear at once (FR-CY-01)
        run_quietly(
            request.app.state.session_factory,
            JobTrigger.PACKAGE_CHANGED,
            client_pks=[client.id],
            requested_by_id=principal.user_id,
        )
    return _filings_out(db, client)


@router.get("/api/admin/filing-changes")
def waiting_filing_changes(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ChangeOut]:
    """Removals waiting for an Administrator, oldest first."""
    return list(
        reversed(_changes(db, ClientFilingChange.status == FilingChangeStatus.PENDING, limit=500))
    )


@router.post("/api/admin/filing-changes/{change_id}/decision")
def decide_filing_change(
    change_id: Annotated[int, Path(ge=1)],
    body: DecisionIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ChangeOut:
    change = db.scalar(
        select(ClientFilingChange)
        .where(ClientFilingChange.id == change_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if change is None:
        raise ApiHTTPException(404, "Not found.", code="not_found")
    try:
        outcome: Outcome = decide(
            db, change, approve=body.approve, note=body.note, user_id=principal.user_id
        )
    except ChangeRefused as exc:
        db.rollback()
        raise ApiHTTPException(exc.status, exc.message, code=exc.code) from None
    client = db.get(Client, change.client_pk)
    filing = db.get(Filing, change.filing_id)
    audit(
        db,
        "client.filing_removed" if body.approve else "client.filing_removal_rejected",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("client", client.client_id if client else change.client_pk),
        details={
            "filing": filing.code if filing else change.filing_id,
            "change": change.id,
            "withdrawn": len(outcome.withdrawn),
        },
    )
    db.commit()
    [out] = _changes(db, ClientFilingChange.id == change.id, limit=1)
    out.withdrawn = outcome.withdrawn
    return out
