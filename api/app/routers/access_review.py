"""The access review (BRD FR-AC-04, chunk C37): who can reach which client, for the Partners to
certify each quarter.

Every client, with each person who has access to it and how:
- its owner (the Staff member or Administrator responsible);
- the person assigned to one of its filings, while that assignment is open (consultants and
  in-house preparers; that filing's work only);
- an Administrator with an active break-glass grant (a recorded reason, for a limited time).
Every active Administrator can reach every client, after giving a reason for one they do not
own; they are listed once, as "All clients". Disabled accounts that still own clients are listed
and marked, so they can be reassigned.

The list and the export show the moment they were generated. Exporting is audited.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.service import ticket_code
from app.models import (
    Assignment,
    BreakGlassGrant,
    ChangeField,
    Client,
    ClientChange,
    Cycle,
    Filing,
    Role,
    Ticket,
    User,
)
from app.reports.files import pdf, xlsx
from app.timeutil import ensure_utc, format_ist, utcnow

router = APIRouter(prefix="/api/admin/access-review", tags=["admin: access review"])

admin_only = require_roles(Role.ADMINISTRATOR)

COLUMNS = ["Client ID", "Client", "Client status", "Person", "Email", "Role", "Access", "Since"]
ROLE_LABEL = {
    Role.ADMINISTRATOR: "Administrator",
    Role.STAFF: "Staff",
    Role.CONSULTANT: "Consultant",
}


class AccessRow(BaseModel):
    client_id: int | None  # None: every client (Administrators)
    client_name: str
    client_status: str | None
    person: str
    email: str
    role: str
    access: str
    since: datetime | None


class ReviewOut(BaseModel):
    generated_at: datetime
    generated_by: str
    rows: list[AccessRow]


class ExportIn(BaseModel):
    format: Literal["xlsx", "pdf"]


def _role(user: User) -> str:
    label = ROLE_LABEL[user.role]
    return label if user.is_active else f"{label} (account disabled)"


def review_rows(db: Session, now: datetime) -> list[AccessRow]:
    """Current access, client by client, then the Administrators."""
    rows: list[AccessRow] = []
    owner_since = dict(
        db.execute(
            select(ClientChange.client_pk, func.max(ClientChange.changed_at))
            .where(ClientChange.field == ChangeField.OWNER)
            .group_by(ClientChange.client_pk)
        ).all()
    )
    for client, owner in db.execute(
        select(Client, User).join(User, User.id == Client.owner_id).order_by(Client.client_id)
    ):
        rows.append(
            AccessRow(
                client_id=client.client_id,
                client_name=client.legal_name,
                client_status=client.status.value,
                person=owner.full_name,
                email=owner.email,
                role=_role(owner),
                access="Owner",
                since=owner_since.get(client.id, client.created_at),
            )
        )
    for assignment, client, user, number, code, period in db.execute(
        select(Assignment, Client, User, Ticket.number, Filing.code, Cycle.period_label)
        .join(Cycle, Cycle.id == Assignment.cycle_id)
        .join(Ticket, Ticket.id == Cycle.ticket_id)
        .join(Filing, Filing.id == Cycle.filing_id)
        .join(Client, Client.id == Cycle.client_pk)
        .join(User, User.id == Assignment.assignee_id)
        .where(Assignment.ended_at.is_(None))
    ):
        rows.append(
            AccessRow(
                client_id=client.client_id,
                client_name=client.legal_name,
                client_status=client.status.value,
                person=user.full_name,
                email=user.email,
                role=_role(user),
                access=f"Assigned {ticket_code(number)} ({code} {period}) only",
                since=assignment.assigned_at,
            )
        )
    for grant, client, user in db.execute(
        select(BreakGlassGrant, Client, User)
        .join(Client, Client.id == BreakGlassGrant.client_pk)
        .join(User, User.id == BreakGlassGrant.user_id)
        .where(BreakGlassGrant.expires_at > now)
    ):
        until = format_ist(ensure_utc(grant.expires_at), "%d %b %Y %H:%M IST")
        rows.append(
            AccessRow(
                client_id=client.client_id,
                client_name=client.legal_name,
                client_status=client.status.value,
                person=user.full_name,
                email=user.email,
                role=_role(user),
                access=f"Break-glass until {until}: {grant.reason}",
                since=grant.granted_at,
            )
        )
    rows.sort(key=lambda r: (r.client_id or 0, r.access != "Owner", r.person))
    for admin in db.scalars(
        select(User)
        .where(User.role == Role.ADMINISTRATOR, User.is_active.is_(True))
        .order_by(User.full_name)
    ):
        rows.append(
            AccessRow(
                client_id=None,
                client_name="All clients",
                client_status=None,
                person=admin.full_name,
                email=admin.email,
                role=_role(admin),
                access="Administrator: every client; a reason is recorded to open one they do not own",
                since=admin.created_at,
            )
        )
    return rows


@router.get("")
def access_review(
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ReviewOut:
    now = utcnow()
    return ReviewOut(generated_at=now, generated_by=principal.full_name, rows=review_rows(db, now))


@router.post("/export")
def export_access_review(
    body: ExportIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """The same list as a file for the Partners to certify, stamped with when it was made."""
    now = utcnow()
    rows = review_rows(db, now)
    stamp = format_ist(now, "%d %b %Y %H:%M IST")
    title = "Access review"
    note = f"Access review generated {stamp} by {principal.full_name}. Certify each quarter."
    table = [
        [
            r.client_id if r.client_id is not None else "All",
            r.client_name,
            r.client_status,
            r.person,
            r.email,
            r.role,
            r.access,
            format_ist(ensure_utc(r.since), "%d %b %Y") if r.since else None,
        ]
        for r in rows
    ]
    audit(
        db,
        "access_review.exported",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("report", "access-review"),
        details={"format": body.format, "rows": len(rows), "generated_at": now.isoformat()},
    )
    db.commit()
    name = f"ledgerline-access-review-{format_ist(now, '%Y-%m-%d-%H%M')}"
    if body.format == "xlsx":
        content = xlsx(title, COLUMNS, table, note=note)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        content = pdf(title, note, COLUMNS, table)
        media = "application/pdf"
    return Response(
        content=content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{name}.{body.format}"',
            "Cache-Control": "no-store",
        },
    )
