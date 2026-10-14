"""Client emails that did not get through (BRD FR-AL-06): shown on the owner's work list.

A client-facing email (an alert, a link or an update) is a problem once it has failed or
bounced, or while it is still being retried after a failed try ("delayed"), until someone marks
it followed up. The work list reads this live, so a failure shows there as soon as the send
worker records it (well within the 15 minutes the BRD allows).
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.orm import Session

from app.models import Email, EmailKind, EmailStatus

CLIENT_KINDS = (EmailKind.CLIENT_ALERT, EmailKind.CLIENT_UPDATE, EmailKind.PORTAL_LINK)

LABEL = {
    "failed": "Email to the client failed: call them",
    "delayed": "Email to the client delayed: still trying",
}


def is_problem() -> ColumnElement[bool]:
    return and_(
        Email.kind.in_(CLIENT_KINDS),
        Email.problem_handled_at.is_(None),
        or_(
            Email.status.in_((EmailStatus.FAILED, EmailStatus.BOUNCED)),
            and_(Email.status == EmailStatus.QUEUED, Email.last_error.is_not(None)),
        ),
    )


def problem_of(email: Email) -> str | None:
    if email.kind not in CLIENT_KINDS or email.problem_handled_at is not None:
        return None
    if email.status in (EmailStatus.FAILED, EmailStatus.BOUNCED):
        return "failed"
    if email.status is EmailStatus.QUEUED and email.last_error:
        return "delayed"
    return None


def problems_for(db: Session, ticket_ids: Iterable[int]) -> dict[int, str]:
    """For each ticket with a problem email: "failed" (the worse) or "delayed"."""
    ids = list(ticket_ids)
    if not ids:
        return {}
    found: dict[int, str] = {}
    for ticket_id, status in db.execute(
        select(Email.ticket_id, Email.status).where(Email.ticket_id.in_(ids), is_problem())
    ):
        if ticket_id is None:  # filtered out by the query; keeps the type checker sure
            continue
        worst = "delayed" if status is EmailStatus.QUEUED else "failed"
        if found.get(ticket_id) != "failed":
            found[ticket_id] = worst
    return found
