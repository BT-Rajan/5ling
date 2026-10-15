"""Fee plans and client-specific overrides (BRD FR-FE-01).

A client's fee is its package version's fee and billing frequency, unless an Administrator
approved a different amount for this client on that package version. Staff (for their own
clients) ask for an override with a reason and it waits for an Administrator; an override an
Administrator enters is approved by them as they enter it. The original amount, the approved
amount and who approved it are kept. Changing the client's package ends an override, since it
was approved against the old package's fee.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    BillingFrequency,
    Client,
    FeeOverride,
    OverrideStatus,
    Package,
    PackageVersion,
    Role,
)
from app.timeutil import utcnow


class OverrideRefused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Plan:
    package: Package
    version: PackageVersion
    frequency: BillingFrequency
    package_fee: int
    override: FeeOverride | None

    @property
    def fee(self) -> int:
        return self.override.amount_paise if self.override else self.package_fee


def current_override(db: Session, client: Client) -> FeeOverride | None:
    return db.scalar(
        select(FeeOverride)
        .where(
            FeeOverride.client_pk == client.id,
            FeeOverride.package_version_id == client.package_version_id,
            FeeOverride.status == OverrideStatus.APPROVED,
        )
        .order_by(FeeOverride.decided_at.desc(), FeeOverride.id.desc())
        .limit(1)
    )


def plan_of(db: Session, client: Client) -> Plan:
    version = db.get(PackageVersion, client.package_version_id)
    package = db.get(Package, version.package_id) if version else None
    if version is None or package is None:
        raise RuntimeError("client refers to a missing package")  # foreign keys prevent it
    if version.fee_paise is None or version.billing_frequency is None:
        raise RuntimeError("client is on an unpublished package version")  # publish requires both
    return Plan(
        package=package,
        version=version,
        frequency=version.billing_frequency,
        package_fee=version.fee_paise,
        override=current_override(db, client),
    )


def pending_for(db: Session, client: Client) -> FeeOverride | None:
    return db.scalar(
        select(FeeOverride).where(
            FeeOverride.client_pk == client.id, FeeOverride.status == OverrideStatus.PENDING
        )
    )


def request_override(
    db: Session, *, client: Client, amount_paise: int, reason: str, user_id: int, role: Role
) -> FeeOverride:
    """Ask for (Staff) or set (Administrator) a client-specific fee. The caller locked the client."""
    if pending_for(db, client) is not None:
        raise OverrideRefused(
            "override_pending", "A fee change for this client is already waiting for approval."
        )
    plan = plan_of(db, client)
    if amount_paise == plan.fee:
        raise OverrideRefused("same_fee", "That is already this client's fee.")
    now = utcnow()
    by_admin = role is Role.ADMINISTRATOR
    override = FeeOverride(
        client_pk=client.id,
        package_version_id=plan.version.id,
        original_paise=plan.package_fee,
        amount_paise=amount_paise,
        reason=reason,
        status=OverrideStatus.APPROVED if by_admin else OverrideStatus.PENDING,
        requested_by_id=user_id,
        requested_at=now,
        decided_by_id=user_id if by_admin else None,
        decided_at=now if by_admin else None,
    )
    db.add(override)
    db.flush()
    return override


def decide(
    db: Session, override_id: int, *, approve: bool, note: str | None, user_id: int
) -> FeeOverride:
    """An Administrator approves or rejects a pending override, once."""
    override = db.scalar(
        select(FeeOverride)
        .where(FeeOverride.id == override_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if override is None:
        raise OverrideRefused("not_found", "Not found.")
    if override.status is not OverrideStatus.PENDING:
        raise OverrideRefused("already_decided", "This fee change has already been decided.")
    client = db.get(Client, override.client_pk)
    if client is None or client.package_version_id != override.package_version_id:
        raise OverrideRefused(
            "package_changed",
            "The client's package has changed since this was asked for; ask again.",
        )
    if not approve and not (note or "").strip():
        raise OverrideRefused("note_required", "Say why it is rejected.")
    override.status = OverrideStatus.APPROVED if approve else OverrideStatus.REJECTED
    override.decided_by_id = user_id
    override.decided_at = utcnow()
    override.decision_note = (note or "").strip() or None
    db.flush()
    return override
