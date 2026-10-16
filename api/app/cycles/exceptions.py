"""Adding or removing a filing for one client (BRD FR-PK-07), chunk C37.

A client's filings are its package version's, plus filings added for it alone, minus filings
removed for it alone (the latest approved change for a filing wins). The cycle generator uses
this set, so an added filing gets its cycles at once and a removed one gets no new cycles.

Adding applies at once. Removing waits for an Administrator, unless an Administrator asked; until
then the client keeps the filing. When a removal is approved, that filing's cycles that never
started (still Scheduled) are closed by the system, with the reason in their history; work that
has started carries on and is finished as usual. Those closings make no client update draft: the
client was not asked for anything and nothing changed for them.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.cycles.service import ticket_code, transition
from app.models import (
    CalendarVersion,
    Client,
    ClientFilingChange,
    Cycle,
    Filing,
    FilingChangeKind,
    FilingChangeStatus,
    PackageFiling,
    Stage,
    StateEvent,
    Ticket,
)
from app.timeutil import utcnow


class ChangeRefused(Exception):
    def __init__(self, code: str, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def package_filings(db: Session, client: Client) -> list[int]:
    return list(
        db.scalars(
            select(PackageFiling.filing_id)
            .where(PackageFiling.package_version_id == client.package_version_id)
            .order_by(PackageFiling.position)
        )
    )


def effective_filings(db: Session, client: Client) -> list[int]:
    """The filings this client gets cycles for: the package's, with its own changes applied."""
    filings = package_filings(db, client)
    approved = db.execute(
        select(ClientFilingChange.filing_id, ClientFilingChange.kind)
        .where(
            ClientFilingChange.client_pk == client.id,
            ClientFilingChange.status == FilingChangeStatus.APPROVED,
        )
        .order_by(ClientFilingChange.decided_at, ClientFilingChange.id)
    ).all()
    latest = dict(approved)  # in decision order, so the latest decision wins
    for filing_id, kind in latest.items():
        if kind is FilingChangeKind.ADD and filing_id not in filings:
            filings.append(filing_id)
        elif kind is FilingChangeKind.REMOVE and filing_id in filings:
            filings.remove(filing_id)
    return filings


@dataclass(frozen=True)
class Outcome:
    change: ClientFilingChange
    withdrawn: list[str]  # ticket codes of the unstarted cycles closed


def request_change(
    db: Session,
    client: Client,
    filing_id: int,
    kind: FilingChangeKind,
    reason: str,
    *,
    user_id: int,
    role: str,
) -> Outcome:
    """Record the request. Adds, and an Administrator's removals, apply at once. Caller commits."""
    filing = db.get(Filing, filing_id)
    if filing is None:
        raise ChangeRefused("not_found", "No such filing.", 404)
    current = effective_filings(db, client)
    if kind is FilingChangeKind.ADD:
        if filing_id in current:
            raise ChangeRefused(
                "already_included", f"{filing.code} is already in this client's filings."
            )
        if not filing.is_active or not db.scalar(
            select(CalendarVersion.id).where(
                CalendarVersion.filing_id == filing_id, CalendarVersion.published_at.is_not(None)
            )
        ):
            raise ChangeRefused(
                "not_ready", f"{filing.code} is not active with a published calendar."
            )
    else:
        if filing_id not in current:
            raise ChangeRefused("not_included", f"{filing.code} is not in this client's filings.")
        if db.scalar(
            select(ClientFilingChange.id).where(
                ClientFilingChange.client_pk == client.id,
                ClientFilingChange.filing_id == filing_id,
                ClientFilingChange.status == FilingChangeStatus.PENDING,
            )
        ):
            raise ChangeRefused("already_pending", "A removal of this filing is already waiting.")
    now = utcnow()
    immediate = kind is FilingChangeKind.ADD or role == "administrator"
    change = ClientFilingChange(
        client_pk=client.id,
        filing_id=filing_id,
        kind=kind,
        status=FilingChangeStatus.APPROVED if immediate else FilingChangeStatus.PENDING,
        reason=reason,
        requested_by_id=user_id,
        requested_at=now,
        decided_by_id=user_id if immediate else None,
        decided_at=now if immediate else None,
    )
    db.add(change)
    db.flush()
    event = {
        (FilingChangeKind.ADD, True): "client.filing_added",
        (FilingChangeKind.REMOVE, True): "client.filing_removed",
        (FilingChangeKind.REMOVE, False): "client.filing_removal_requested",
    }[(kind, immediate)]
    audit(
        db,
        event,
        actor_user_id=user_id,
        actor_role=role,
        target=("client", client.client_id),
        details={"filing": filing.code, "change": change.id, "reason": reason},
    )
    withdrawn = (
        _withdraw(db, client, filing, change)
        if immediate and kind is FilingChangeKind.REMOVE
        else []
    )
    return Outcome(change=change, withdrawn=withdrawn)


def decide(
    db: Session, change: ClientFilingChange, *, approve: bool, note: str | None, user_id: int
) -> Outcome:
    """An Administrator decides a pending removal. Caller has locked it, audits and commits."""
    if change.status is not FilingChangeStatus.PENDING:
        raise ChangeRefused("already_decided", "This request has already been decided.")
    note = (note or "").strip() or None
    if not approve and note is None:
        raise ChangeRefused("note_required", "Say why it is refused.", 422)
    change.status = FilingChangeStatus.APPROVED if approve else FilingChangeStatus.REJECTED
    change.decided_by_id = user_id
    change.decided_at = utcnow()
    change.decision_note = note
    db.flush()
    client = db.get(Client, change.client_pk)
    filing = db.get(Filing, change.filing_id)
    if client is None or filing is None:  # foreign keys prevent it
        raise RuntimeError("filing change refers to missing rows")
    withdrawn = _withdraw(db, client, filing, change) if approve else []
    return Outcome(change=change, withdrawn=withdrawn)


def _withdraw(db: Session, client: Client, filing: Filing, change: ClientFilingChange) -> list[str]:
    """Close this filing's unstarted cycles for this client (system moves, gated in transition)."""
    closed: list[str] = []
    cycles = db.scalars(
        select(Cycle)
        .where(
            Cycle.client_pk == client.id,
            Cycle.filing_id == filing.id,
            Cycle.stage == Stage.SCHEDULED,
        )
        .order_by(Cycle.period_start)
        .with_for_update()
    ).all()
    for cycle in cycles:
        ticket = db.get(Ticket, cycle.ticket_id)
        if ticket is None:  # a foreign key prevents it
            continue
        transition(
            db,
            cycle,
            ticket,
            "withdraw",
            actor_id=None,
            actor_role=None,
            note=f"{filing.code} removed for this client: {change.reason}"[:1000],
        )
        closed.append(ticket_code(ticket.number))
    if closed:
        db.flush()
        # Nothing for the client to hear about: mark these closings' events as handled, so the
        # update drafter (CR-03) passes over them.
        now = utcnow()
        for event in db.scalars(
            select(StateEvent).where(
                StateEvent.client_pk == client.id,
                StateEvent.handled_at.is_(None),
                StateEvent.from_state == Stage.SCHEDULED.value,
                StateEvent.to_state == Stage.CLOSED.value,
            )
        ):
            event.handled_at = now
    return closed
