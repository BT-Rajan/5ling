"""The stage list and the only moves allowed between stages (BRD FR-CY-02, FR-WF-04).

Each stage moves to the next one only; nothing can be skipped. The only moves backwards are
returning work from review to the preparer (FR-WF-04) and withdrawing an assignment (FR-WF-02).
Moves that later chunks guard further (maker-checker in C22, filing proof in C23) are listed here
already; those chunks add their checks to `app.cycles.service.transition`.

Nil filings (FR-CY-03, chunk C34) will add one more move, Awaiting documents to In review, gated
on the client's stored confirmation.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import ClientStatus, Stage, TaskStatus

ORDER: tuple[Stage, ...] = tuple(Stage)

STAGE_LABEL: dict[Stage, str] = {
    Stage.SCHEDULED: "Scheduled",
    Stage.AWAITING_DOCUMENTS: "Awaiting documents",
    Stage.DOCUMENTS_RECEIVED: "Documents received",
    Stage.ASSIGNED: "Assigned",
    Stage.PREPARING: "Preparing",
    Stage.IN_REVIEW: "In review",
    Stage.READY_TO_FILE: "Ready to file",
    Stage.FILED: "Filed",
    Stage.ACKNOWLEDGED: "Acknowledged",
    Stage.CLOSED: "Closed",
}


@dataclass(frozen=True)
class Move:
    action: str
    label: str
    source: Stage
    target: Stage
    needs_note: bool = False


MOVES: tuple[Move, ...] = (
    Move(
        "request_documents",
        "Ask the client for documents",
        Stage.SCHEDULED,
        Stage.AWAITING_DOCUMENTS,
    ),
    Move(
        "documents_received",
        "Mark documents received",
        Stage.AWAITING_DOCUMENTS,
        Stage.DOCUMENTS_RECEIVED,
    ),
    Move("assign", "Assign", Stage.DOCUMENTS_RECEIVED, Stage.ASSIGNED),
    Move(
        "withdraw_assignment",
        "Withdraw the assignment",
        Stage.ASSIGNED,
        Stage.DOCUMENTS_RECEIVED,
        needs_note=True,
    ),
    Move("start_preparing", "Start preparing", Stage.ASSIGNED, Stage.PREPARING),
    Move("submit_for_review", "Send for review", Stage.PREPARING, Stage.IN_REVIEW),
    Move("approve", "Approve", Stage.IN_REVIEW, Stage.READY_TO_FILE),
    Move("return", "Return to the preparer", Stage.IN_REVIEW, Stage.PREPARING, needs_note=True),
    Move("file", "Mark filed", Stage.READY_TO_FILE, Stage.FILED),
    Move("acknowledge", "Mark acknowledged", Stage.FILED, Stage.ACKNOWLEDGED),
    Move("close", "Close", Stage.ACKNOWLEDGED, Stage.CLOSED),
)

BY_ACTION: dict[str, Move] = {m.action: m for m in MOVES}


def moves_from(stage: Stage) -> list[Move]:
    return [m for m in MOVES if m.source is stage]


def _check_table() -> None:
    """Forward moves go exactly one stage on; backward moves are only the listed exceptions."""
    backward = {"withdraw_assignment", "return"}
    for move in MOVES:
        step = ORDER.index(move.target) - ORDER.index(move.source)
        if move.action in backward:
            if step >= 0 or not move.needs_note:
                raise AssertionError(f"{move.action} must go back, with a note")
        elif step != 1:
            raise AssertionError(f"{move.action} skips a stage")
    if len(BY_ACTION) != len(MOVES):
        raise AssertionError("duplicate action names")


_check_table()


TASK_LABEL: dict[TaskStatus, str] = {
    TaskStatus.OPEN: "Open",
    TaskStatus.DONE: "Done",
    TaskStatus.CANCELLED: "Cancelled",
}

CLIENT_STATUS_LABEL: dict[ClientStatus, str] = {
    ClientStatus.ACTIVE: "Active",
    ClientStatus.PAUSED: "Paused",
    ClientStatus.CLOSED: "Closed",
}
