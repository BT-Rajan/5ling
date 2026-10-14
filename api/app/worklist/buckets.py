"""The daily work list's four lists (BRD FR-IN-04), defined once.

The work list and the count report both build on these conditions, so their numbers cannot
drift apart (FR-IN-04: counts on the work list equal counts in reports for the same owner and
date). Only clients that are not closed count. A cycle can be in more than one list (missing
and overdue, say).

- received: documents are in and work has not started (stage Documents received);
- missing: still collecting, the first alert went out at least the grace period ago, and a
  required document has not arrived (FR-IN-02);
- due this week: not yet filed, due between Monday and Sunday of the week (India time);
- overdue: not yet filed, and the due date has passed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import ColumnElement, and_, exists, func, select
from sqlalchemy.orm import Session

from app.models import (
    AlertKind,
    Client,
    ClientStatus,
    Cycle,
    CycleAlert,
    CycleChecklistItem,
    DocumentVersion,
    ScanStatus,
    Stage,
)

Bucket = Literal["received", "missing", "due_this_week", "overdue"]
BUCKETS: tuple[Bucket, ...] = ("received", "missing", "due_this_week", "overdue")
NOT_FILED = (
    Stage.SCHEDULED,
    Stage.AWAITING_DOCUMENTS,
    Stage.DOCUMENTS_RECEIVED,
    Stage.ASSIGNED,
    Stage.PREPARING,
    Stage.IN_REVIEW,
    Stage.READY_TO_FILE,
)
COLLECTING = (Stage.SCHEDULED, Stage.AWAITING_DOCUMENTS)


@dataclass(frozen=True)
class Scope:
    on: date  # the day the list is for
    owner_id: int | None  # None: every owner (the Administrator's view)
    grace_days: int


def week_of(day: date) -> tuple[date, date]:
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def required_missing() -> ColumnElement[bool]:
    """A required checklist item of this cycle has no file that passed the virus check."""
    clean = exists().where(
        DocumentVersion.document_id == CycleChecklistItem.document_id,
        DocumentVersion.scan_status == ScanStatus.CLEAN,
    )
    return exists().where(
        CycleChecklistItem.cycle_id == Cycle.id,
        CycleChecklistItem.required.is_(True),
        ~clean,
    )


def condition(bucket: Bucket, scope: Scope) -> ColumnElement[bool]:
    if bucket == "received":
        return Cycle.stage == Stage.DOCUMENTS_RECEIVED
    if bucket == "missing":
        first_alert_due = exists().where(
            CycleAlert.cycle_id == Cycle.id,
            CycleAlert.kind == AlertKind.FIRST,
            CycleAlert.sent_on <= scope.on - timedelta(days=scope.grace_days),
        )
        return and_(Cycle.stage.in_(COLLECTING), first_alert_due, required_missing())
    if bucket == "due_this_week":
        monday, sunday = week_of(scope.on)
        return and_(Cycle.stage.in_(NOT_FILED), Cycle.due_date.between(monday, sunday))
    return and_(Cycle.stage.in_(NOT_FILED), Cycle.due_date < scope.on)


def in_scope(scope: Scope) -> ColumnElement[bool]:
    base = Client.status != ClientStatus.CLOSED
    return and_(base, Client.owner_id == scope.owner_id) if scope.owner_id is not None else base


def counts(db: Session, scope: Scope) -> dict[Bucket, int]:
    """One COUNT per list: what the reports show (and the work list must match)."""
    out: dict[Bucket, int] = {}
    for bucket in BUCKETS:
        out[bucket] = int(
            db.scalar(
                select(func.count(Cycle.id))
                .join(Client, Client.id == Cycle.client_pk)
                .where(in_scope(scope), condition(bucket, scope))
            )
            or 0
        )
    return out
