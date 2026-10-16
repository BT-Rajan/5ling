"""Rules an Administrator edits, and break-glass access (BRD NFR-MN-01, FR-AC-05)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.breakglass import grant, needs_reason
from app.auth.deps import Principal, get_db, require_roles
from app.errors import ApiHTTPException
from app.models import (
    FIRST_CLIENT_ID,
    LAST_CLIENT_ID,
    RULE_LIMITS,
    BreakGlassGrant,
    Client,
    Role,
    User,
)
from app.routers.clients import load_client
from app.rules import rules
from app.timeutil import utcnow

router = APIRouter(tags=["rules"])

admin_only = require_roles(Role.ADMINISTRATOR)

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


def _limit(name: str) -> object:
    _, lo, hi = RULE_LIMITS[name]
    return Field(ge=lo, le=hi)


class RulesIn(BaseModel):
    waiting_grace_days: Annotated[int, _limit("waiting_grace_days")]
    reminder_interval_days: Annotated[int, _limit("reminder_interval_days")]
    escalation_days_before: Annotated[int, _limit("escalation_days_before")]
    portal_link_hours: Annotated[int, _limit("portal_link_hours")]
    idle_timeout_minutes: Annotated[int, _limit("idle_timeout_minutes")]
    break_glass_minutes: Annotated[int, _limit("break_glass_minutes")]
    fee_reminder_interval_days: Annotated[int, _limit("fee_reminder_interval_days")]


class Limit(BaseModel):
    default: int
    min: int
    max: int


class RulesOut(RulesIn):
    limits: dict[str, Limit]
    updated_by: str | None
    updated_at: datetime


class BreakGlassIn(BaseModel):
    reason: Reason


class GrantOut(BaseModel):
    id: int
    client_id: int
    client_name: str
    administrator: str
    reason: str
    granted_at: datetime
    expires_at: datetime
    active: bool


def _rules_out(db: Session) -> RulesOut:
    row = rules(db)
    by = db.get(User, row.updated_by_id) if row.updated_by_id else None
    return RulesOut(
        **{name: getattr(row, name) for name in RULE_LIMITS},
        limits={n: Limit(default=d, min=lo, max=hi) for n, (d, lo, hi) in RULE_LIMITS.items()},
        updated_by=by.full_name if by else None,
        updated_at=row.updated_at,
    )


@router.get("/api/admin/rules")
def get_rules(
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> RulesOut:
    return _rules_out(db)


@router.put("/api/admin/rules")
def set_rules(
    body: RulesIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> RulesOut:
    """NFR-MN-01: change the timings; they apply from the next request or job run."""
    row = rules(db, lock=True)
    before = {name: getattr(row, name) for name in RULE_LIMITS}
    after = body.model_dump()
    if before == after:
        raise ApiHTTPException(409, "Nothing has changed.", code="no_change")
    for name, value in after.items():
        setattr(row, name, value)
    row.updated_by_id = principal.user_id
    row.updated_at = utcnow()
    audit(
        db,
        "rules.changed",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("rules", 1),
        details={n: f"{before[n]} -> {after[n]}" for n in RULE_LIMITS if before[n] != after[n]},
    )
    db.commit()
    return _rules_out(db)


@router.post("/api/clients/{client_id}/break-glass", status_code=201)
def break_glass(
    client_id: Annotated[int, Path(ge=FIRST_CLIENT_ID, le=LAST_CLIENT_ID)],
    body: BreakGlassIn,
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> GrantOut:
    """FR-AC-05: open a client you do not own, with a reason, flagged in the audit log."""
    client = load_client(db, principal, client_id, skip_reason=True)
    if not needs_reason(principal, client):
        raise ApiHTTPException(409, "This is your own client.", code="own_client")
    row = grant(db, principal, client, body.reason)
    audit(
        db,
        "client.break_glass",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("client", client.client_id),
        details={
            "flagged": True,
            "reason": body.reason,
            "owner_id": client.owner_id,
            "until": row.expires_at.isoformat(),
        },
    )
    db.commit()
    return _grant_out(row, client, principal.full_name)


def _grant_out(row: BreakGlassGrant, client: Client, name: str) -> GrantOut:
    return GrantOut(
        id=row.id,
        client_id=client.client_id,
        client_name=client.legal_name,
        administrator=name,
        reason=row.reason,
        granted_at=row.granted_at,
        expires_at=row.expires_at,
        active=row.expires_at > utcnow(),
    )


@router.get("/api/admin/break-glass")
def recent_break_glass(
    principal: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[GrantOut]:
    """Every break-glass opening, newest first: for the Partners to review."""
    rows = db.execute(
        select(BreakGlassGrant, Client, User.full_name)
        .join(Client, Client.id == BreakGlassGrant.client_pk)
        .join(User, User.id == BreakGlassGrant.user_id)
        .order_by(BreakGlassGrant.id.desc())
        .limit(200)
    ).all()
    return [_grant_out(g, c, n) for g, c, n in rows]
