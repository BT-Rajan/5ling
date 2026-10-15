"""Break-glass access (BRD FR-AC-05).

An Administrator can open any client, but opening one they do not own needs a reason first.
Giving it records a grant for that Administrator and that client, for a limited time (a rule,
default 4 hours), flagged in the audit log; until then the client's record, tickets, documents
and fees answer "reason required". Lists, the dashboard and reports are not opening a client.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal
from app.errors import ApiHTTPException
from app.models import BreakGlassGrant, Client, Role
from app.rules import rules
from app.timeutil import utcnow


def needs_reason(principal: Principal, client: Client) -> bool:
    return principal.role is Role.ADMINISTRATOR and client.owner_id != principal.user_id


def active_grant(
    db: Session, user_id: int, client_pk: int, now: datetime | None = None
) -> BreakGlassGrant | None:
    return db.scalar(
        select(BreakGlassGrant)
        .where(
            BreakGlassGrant.user_id == user_id,
            BreakGlassGrant.client_pk == client_pk,
            BreakGlassGrant.expires_at > (now or utcnow()),
        )
        .order_by(BreakGlassGrant.expires_at.desc())
        .limit(1)
    )


def require_open(db: Session, principal: Principal, client: Client) -> None:
    """Refuse an Administrator who has not given a reason for a client they do not own."""
    if needs_reason(principal, client) and active_grant(db, principal.user_id, client.id) is None:
        raise ApiHTTPException(
            403,
            "This client is not yours. Say why you are opening it; it is recorded.",
            code="reason_required",
            extra={"client_id": client.client_id},
        )


def grant(db: Session, principal: Principal, client: Client, reason: str) -> BreakGlassGrant:
    now = utcnow()
    row = BreakGlassGrant(
        user_id=principal.user_id,
        client_pk=client.id,
        reason=reason,
        granted_at=now,
        expires_at=now + timedelta(minutes=rules(db).break_glass_minutes),
    )
    db.add(row)
    db.flush()
    return row
