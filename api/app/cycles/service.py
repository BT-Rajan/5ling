"""Creating tickets and cycles, and moving a cycle's stage (BRD FR-CY-02, CR-02, CR-03)."""

from __future__ import annotations

import re
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit import audit
from app.cycles.stages import BY_ACTION, STAGE_LABEL, Move, moves_from
from app.models import (
    Assignment,
    CalendarVersion,
    ChecklistItem,
    Client,
    Cycle,
    CycleChecklistItem,
    EndReason,
    EventKind,
    Filing,
    PackageFiling,
    Stage,
    StageChange,
    StateEvent,
    Ticket,
    TicketCounter,
    TicketType,
)
from app.timeutil import utcnow

_TICKET = re.compile(r"^(?:TKT-?)?0*([1-9][0-9]{0,8})$", re.IGNORECASE)


def ticket_code(number: int) -> str:
    return f"TKT-{number:06d}"


def parse_ticket(text: str) -> int | None:
    """ "TKT-000123", "tkt123" or "123" -> 123; anything else -> None."""
    match = _TICKET.match(text.strip())
    return int(match.group(1)) if match else None


class MoveRefused(Exception):
    """A stage move the rules do not allow. `code` is for the API; the message is for people."""

    def __init__(self, code: str, message: str, allowed: list[Move]) -> None:
        super().__init__(message)
        self.code = code
        self.allowed = allowed


def _issue_ticket_number(db: Session) -> int:
    counter = TicketCounter(issued_at=utcnow())
    db.add(counter)
    db.flush()
    return counter.number


def create_ticket(
    db: Session, *, client: Client, kind: TicketType, title: str, created_by_id: int | None
) -> Ticket:
    ticket = Ticket(
        number=_issue_ticket_number(db),
        type=kind,
        client_pk=client.id,
        title=title[:200],
        created_by_id=created_by_id,
        created_at=utcnow(),
    )
    db.add(ticket)
    db.flush()
    return ticket


def emit_state_event(
    db: Session,
    kind: EventKind,
    *,
    client_pk: int,
    ticket_id: int | None,
    from_state: str,
    to_state: str,
) -> None:
    """CR-03: one event per state change, in the same transaction as the change itself."""
    db.add(
        StateEvent(
            kind=kind,
            client_pk=client_pk,
            ticket_id=ticket_id,
            from_state=from_state,
            to_state=to_state,
            occurred_at=utcnow(),
        )
    )


def create_cycle(
    db: Session,
    *,
    client: Client,
    filing: Filing,
    calendar_version: CalendarVersion,
    package_version_id: int,
    period_key: str,
    period_label: str,
    period_start: date,
    period_end: date,
    due_date: date,
    alert_date: date,
    created_by_id: int | None = None,
) -> Cycle:
    """One filing, one period, one client, with its FILING ticket (CR-02). Starts Scheduled."""
    ticket = create_ticket(
        db,
        client=client,
        kind=TicketType.FILING,
        title=f"{filing.code} {period_label}",
        created_by_id=created_by_id,
    )
    now = utcnow()
    cycle = Cycle(
        ticket_id=ticket.id,
        client_pk=client.id,
        filing_id=filing.id,
        package_version_id=package_version_id,
        calendar_version_id=calendar_version.id,
        period_key=period_key,
        period_label=period_label,
        period_start=period_start,
        period_end=period_end,
        due_date=due_date,
        alert_date=alert_date,
        stage=Stage.SCHEDULED,
        stage_changed_at=now,
        created_at=now,
    )
    db.add(cycle)
    db.flush()
    # FR-AL-04, FR-PK-05: the cycle's own copy of the documents to ask for.
    for position, label, required in db.execute(
        select(ChecklistItem.position, ChecklistItem.label, ChecklistItem.required)
        .join(PackageFiling, PackageFiling.id == ChecklistItem.package_filing_id)
        .where(
            PackageFiling.package_version_id == package_version_id,
            PackageFiling.filing_id == filing.id,
        )
        .order_by(ChecklistItem.position)
    ):
        db.add(
            CycleChecklistItem(cycle_id=cycle.id, position=position, label=label, required=required)
        )
    db.add(
        StageChange(
            cycle_id=cycle.id,
            from_stage=None,
            to_stage=Stage.SCHEDULED,
            action="created",
            changed_by_id=created_by_id,
            changed_at=now,
        )
    )
    audit(
        db,
        "cycle.created",
        actor_user_id=created_by_id,
        target=("ticket", ticket_code(ticket.number)),
        details={"client_id": client.client_id, "filing": filing.code, "period": period_key},
    )
    return cycle


def transition(
    db: Session,
    cycle: Cycle,
    ticket: Ticket,
    action: str,
    *,
    actor_id: int | None,
    actor_role: str | None,
    expected_stage: Stage | None = None,
    note: str | None = None,
) -> Move:
    """Move the cycle by `action`, or refuse. The caller has locked the cycle row."""
    allowed = moves_from(cycle.stage)
    move = BY_ACTION.get(action)
    if expected_stage is not None and expected_stage is not cycle.stage:
        # Someone else moved it, or this is a second click: do nothing rather than move twice.
        raise MoveRefused(
            "stage_changed",
            f"This has already moved to {STAGE_LABEL[cycle.stage]}. Refresh to see it.",
            allowed,
        )
    if move is None or move.source is not cycle.stage:
        raise MoveRefused(
            "illegal_transition",
            f"A cycle in {STAGE_LABEL[cycle.stage]} cannot be moved that way. Stages cannot be skipped.",
            allowed,
        )
    note = (note or "").strip() or None
    if move.needs_note and note is None:
        raise MoveRefused("note_required", "Add a note saying why.", allowed)

    previous = cycle.stage
    now = utcnow()
    cycle.stage = move.target
    cycle.stage_changed_at = now
    db.add(
        StageChange(
            cycle_id=cycle.id,
            from_stage=previous,
            to_stage=move.target,
            action=move.action,
            note=note,
            changed_by_id=actor_id,
            changed_at=now,
        )
    )
    emit_state_event(
        db,
        EventKind.CYCLE_STAGE,
        client_pk=cycle.client_pk,
        ticket_id=ticket.id,
        from_state=previous.value,
        to_state=move.target.value,
    )
    audit(
        db,
        "cycle.stage_changed",
        actor_user_id=actor_id,
        actor_role=actor_role,
        target=("ticket", ticket_code(ticket.number)),
        details={"from": previous.value, "to": move.target.value, "action": move.action},
    )
    if move.target is Stage.CLOSED:
        # FR-WF-02: closing a cycle ends the assignee's access at once.
        db.execute(
            update(Assignment)
            .where(Assignment.cycle_id == cycle.id, Assignment.ended_at.is_(None))
            .values(ended_at=now, end_reason=EndReason.CYCLE_CLOSED)
        )
    return move
