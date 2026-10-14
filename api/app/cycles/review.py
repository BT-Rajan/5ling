"""Maker-checker review (BRD FR-WF-03, FR-WF-04).

The preparers of a cycle are everyone who moved it into Preparing or sent it for review, and
whoever it is assigned to now. None of them may approve it: approving takes the client's owner
or an Administrator who did not prepare it (BRD section 8, "Approve a filing (as checker)": Own,
not as preparer). The approve action is left out of a preparer's choices, and the server refuses
it if it is attempted anyway.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal
from app.models import Assignment, Client, Role, StageChange

PREPARING_ACTIONS = ("start_preparing", "submit_for_review")


def preparers(db: Session, cycle_id: int) -> set[int]:
    movers = set(
        db.scalars(
            select(StageChange.changed_by_id).where(
                StageChange.cycle_id == cycle_id,
                StageChange.action.in_(PREPARING_ACTIONS),
                StageChange.changed_by_id.is_not(None),
            )
        )
    )
    assignee = db.scalar(
        select(Assignment.assignee_id).where(
            Assignment.cycle_id == cycle_id, Assignment.ended_at.is_(None)
        )
    )
    return {int(m) for m in movers if m is not None} | ({assignee} if assignee else set())


def approval_refusal(
    db: Session, principal: Principal, client: Client, cycle_id: int
) -> str | None:
    """Why this person may not approve this cycle, or None if they may."""
    if principal.role is not Role.ADMINISTRATOR and client.owner_id != principal.user_id:
        return "Only the client's owner or an Administrator can approve."
    if principal.user_id in preparers(db, cycle_id):
        return "You prepared this work, so someone else must approve it."
    return None
