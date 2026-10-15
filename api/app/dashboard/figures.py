"""The dashboard's figures (BRD FR-RP-01, FR-RP-02), each defined once.

Every figure is a query; its count and the list behind it are the same query, counted or
listed, so a figure can never disagree with its list. The scope is the work list's: the
Administrator sees every owner (or one), Staff only their own clients, and closed clients are
left out of the work figures (fees still owed by a closed client are still shown).

- cycles by stage: every stage but Closed;
- overdue filings and documents missing: the work list's "overdue" and "missing" lists;
- workload per person: cycles not yet filed and due within 30 days (or already overdue), each
  counted for whoever has it: the open assignee, else the client's owner;
- fees due and fees overdue: invoices with something still owed, before and after their due
  date.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from sqlalchemy import ColumnElement, Select, and_, case, func, select
from sqlalchemy.orm import Session

from app.models import (
    Assignment,
    Client,
    Cycle,
    EntryKind,
    Filing,
    Invoice,
    LedgerEntry,
    Stage,
    Ticket,
)
from app.worklist.buckets import NOT_FILED, Scope, condition, in_scope

Figure = Literal[
    "stage", "overdue_filings", "documents_missing", "workload", "fees_due", "fees_overdue"
]
CYCLE_FIGURES = ("stage", "overdue_filings", "documents_missing", "workload")
OPEN_STAGES = tuple(s for s in Stage if s is not Stage.CLOSED)
WORKLOAD_DAYS = 30


def _person() -> ColumnElement[Any]:
    return func.coalesce(Assignment.assignee_id, Client.owner_id)


def _cycles(scope: Scope) -> Select[Any]:
    return (
        select(Cycle, Client, Ticket.number, Filing.code, _person().label("person_id"))
        .join(Client, Client.id == Cycle.client_pk)
        .join(Ticket, Ticket.id == Cycle.ticket_id)
        .join(Filing, Filing.id == Cycle.filing_id)
        .outerjoin(Assignment, and_(Assignment.cycle_id == Cycle.id, Assignment.ended_at.is_(None)))
        .where(in_scope(scope))
    )


def _workload(scope: Scope) -> ColumnElement[bool]:
    return and_(
        Cycle.stage.in_(NOT_FILED),
        Cycle.due_date <= scope.on + timedelta(days=WORKLOAD_DAYS),
    )


def cycle_query(scope: Scope, figure: Figure, key: str | None = None) -> Select[Any]:
    """The cycles behind a figure. `key` is the stage, or the person's user id, if needed."""
    q = _cycles(scope)
    if figure == "stage":
        stage = Stage(key) if key in {s.value for s in OPEN_STAGES} else None
        if stage is None:
            raise ValueError("unknown stage")
        return q.where(Cycle.stage == stage)
    if figure == "overdue_filings":
        return q.where(condition("overdue", scope))
    if figure == "documents_missing":
        return q.where(condition("missing", scope))
    if figure == "workload":
        if key is None or not key.isdigit():
            raise ValueError("workload needs a person")
        return q.where(_workload(scope), _person() == int(key))
    raise ValueError("not a cycle figure")


def _paid() -> Any:
    signed = case(
        (LedgerEntry.kind == EntryKind.REVERSAL, -LedgerEntry.amount_paise),
        else_=LedgerEntry.amount_paise,
    )
    return (
        select(LedgerEntry.invoice_id, func.sum(signed).label("paid"))
        .group_by(LedgerEntry.invoice_id)
        .subquery()
    )


def invoice_query(scope: Scope, figure: Figure) -> Select[Any]:
    paid = _paid()
    balance = (Invoice.total_paise - func.coalesce(paid.c.paid, 0)).label("balance")
    q: Select[Any] = (
        select(Invoice, Client, balance)  # type: ignore[assignment]
        .join(Client, Client.id == Invoice.client_pk)
        .outerjoin(paid, paid.c.invoice_id == Invoice.id)
        .where(Invoice.total_paise - func.coalesce(paid.c.paid, 0) > 0)
    )
    if scope.owner_id is not None:
        q = q.where(Client.owner_id == scope.owner_id)
    if figure == "fees_due":
        return q.where(Invoice.due_date >= scope.on)
    if figure == "fees_overdue":
        return q.where(Invoice.due_date < scope.on)
    raise ValueError("not a fee figure")


def count_of(db: Session, query: Select[Any]) -> int:
    return int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)


def amount_of(db: Session, query: Select[Any]) -> int:
    sub = query.subquery()
    return int(db.scalar(select(func.coalesce(func.sum(sub.c.balance), 0))) or 0)


def stage_counts(db: Session, scope: Scope) -> dict[Stage, int]:
    sub = _cycles(scope).subquery()
    rows = db.execute(select(sub.c.stage, func.count()).group_by(sub.c.stage)).all()
    found = {Stage(s): int(n) for s, n in rows}
    return {s: found.get(s, 0) for s in OPEN_STAGES}


def workload_counts(db: Session, scope: Scope) -> dict[int, int]:
    sub = _cycles(scope).where(_workload(scope)).subquery()
    rows = db.execute(select(sub.c.person_id, func.count()).group_by(sub.c.person_id)).all()
    return {int(p): int(n) for p, n in rows}
