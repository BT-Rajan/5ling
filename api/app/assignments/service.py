"""Assigning a cycle's work (BRD FR-WF-01, FR-WF-02).

Assigning a cycle that is in Documents received moves it to Assigned; withdrawing an assignment
from an Assigned cycle moves it back (with the reason as the stage note). A new assignment ends
the open one. Closing the cycle ends it too (see `end_on_close`, called by the stage move). The
assignee is notified in the app, and a consultant also by email (they may not be signed in).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.config import Settings
from app.cycles.service import ticket_code, transition
from app.mail.outbox import create
from app.mail.templates import render
from app.mail.updates import draft_updates
from app.models import (
    Assignment,
    Client,
    Cycle,
    EmailKind,
    EndReason,
    Filing,
    NotificationKind,
    Role,
    Stage,
    Ticket,
    User,
)
from app.notify.service import notify
from app.timeutil import utcnow


class AssignRefused(ValueError):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def open_assignment(db: Session, cycle_id: int, *, lock: bool = False) -> Assignment | None:
    stmt = select(Assignment).where(Assignment.cycle_id == cycle_id, Assignment.ended_at.is_(None))
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return db.scalar(stmt)


def can_be_assigned(user: User | None) -> bool:
    if user is None or not user.is_active:
        return False
    if user.role is Role.CONSULTANT:
        return user.agreement_signed_on is not None  # BRD section 12: agreement first
    return user.role in (Role.STAFF, Role.ADMINISTRATOR)


def _end(
    db: Session, a: Assignment, reason: EndReason, by_id: int | None, note: str | None
) -> None:
    a.ended_at = utcnow()
    a.ended_by_id = by_id
    a.end_reason = reason
    a.end_note = note


def assign(
    db: Session,
    settings: Settings,
    *,
    cycle: Cycle,
    ticket: Ticket,
    client: Client,
    assignee: User,
    work_due: date | None,
    note: str | None,
    by_id: int,
    by_name: str,
    by_role: str,
) -> Assignment:
    """The caller holds the cycle's row lock, and audits nothing more; it commits."""
    if cycle.stage in (Stage.FILED, Stage.ACKNOWLEDGED, Stage.CLOSED):
        raise AssignRefused("filed", "This filing is done; it cannot be assigned now.")
    if not can_be_assigned(assignee):
        raise AssignRefused(
            "bad_assignee",
            "Assign to an active staff member, or to a consultant with a signed agreement.",
            422,
        )
    current = open_assignment(db, cycle.id, lock=True)
    if current is not None:
        if current.assignee_id == assignee.id:
            raise AssignRefused(
                "already_assigned", f"This is already assigned to {assignee.full_name}."
            )
        _end(db, current, EndReason.REASSIGNED, by_id, None)
        db.flush()  # free the cycle's one open slot before the new row takes it
    a = Assignment(
        cycle_id=cycle.id,
        assignee_id=assignee.id,
        work_due_date=work_due,
        note=note,
        assigned_by_id=by_id,
        assigned_at=utcnow(),
    )
    db.add(a)
    db.flush()
    if cycle.stage is Stage.DOCUMENTS_RECEIVED:
        transition(db, cycle, ticket, "assign", actor_id=by_id, actor_role=by_role)
    code = ticket_code(ticket.number)
    filing = db.get(Filing, cycle.filing_id)
    what = f"{filing.code if filing else ''} {cycle.period_label}".strip()
    notify(
        db,
        kind=NotificationKind.ASSIGNED,
        dedupe_key=f"assigned:{a.id}",
        client_pk=client.id,
        cycle_id=cycle.id,
        user_id=assignee.id,
        title=f"Assigned to you: {client.legal_name}, {what}",
        body=f"{by_name} assigned {code} to you."
        + (f" Work due {work_due:%d %b %Y}." if work_due else "")
        + (f"\n{note}" if note else ""),
    )
    if assignee.role is Role.CONSULTANT:
        content = render(
            "assignment",
            {
                "firm_name": settings.firm_name,
                "recipient_name": assignee.full_name,
                "sender_name": by_name,
                "filing": filing.code if filing else "",
                "period": cycle.period_label,
                "ticket_id": code,
                "due_date": work_due if work_due else "not set",
                "note": note,
                "link": f"{settings.public_url}/",
            },
        )
        create(db, kind=EmailKind.ASSIGNMENT, to=[assignee.email], content=content, queue_now=True)
    audit(
        db,
        "assignment.created",
        actor_user_id=by_id,
        actor_role=by_role,
        target=("ticket", code),
        details={
            "assignment": a.id,
            "assignee": assignee.id,
            "consultant": assignee.role is Role.CONSULTANT,
            "replaced": current.id if current else None,
        },
    )
    draft_updates(db, settings.firm_name, client_pk=client.id)
    return a


def withdraw(
    db: Session,
    settings: Settings,
    *,
    cycle: Cycle,
    ticket: Ticket,
    client: Client,
    note: str,
    by_id: int,
    by_role: str,
) -> Assignment:
    """End the open assignment now; the assignee loses access at their next request."""
    current = open_assignment(db, cycle.id, lock=True)
    if current is None:
        raise AssignRefused("not_assigned", "This filing is not assigned to anyone.")
    _end(db, current, EndReason.WITHDRAWN, by_id, note)
    if cycle.stage is Stage.ASSIGNED:
        transition(
            db, cycle, ticket, "withdraw_assignment", actor_id=by_id, actor_role=by_role, note=note
        )
    audit(
        db,
        "assignment.withdrawn",
        actor_user_id=by_id,
        actor_role=by_role,
        target=("ticket", ticket_code(ticket.number)),
        details={"assignment": current.id, "assignee": current.assignee_id, "note": note},
    )
    draft_updates(db, settings.firm_name, client_pk=client.id)
    return current
