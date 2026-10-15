"""The reports (BRD FR-RP-03), under the same access rules as the screens (FR-RP-04).

Every report takes a scope: the Administrator's (everyone, or one owner) or a Staff member's
(their own clients only, whatever they ask for). Each returns columns and rows of plain values,
which the API sends as JSON and the exports write as a spreadsheet or PDF.

- filing status by period: for each filing and period with a due date in the range, how many
  cycles are not started, in progress, filed on time, filed late, and overdue;
- late filings: cycles due in the range that were filed after their due date (with the reason
  given when filing) or are past due and not yet filed;
- fee aging: what each client owes today, by how long it has been past due;
- consultant turnaround: for work assigned in the range, per person, how long it took to be sent
  for review;
- alert delivery: client alerts and reminders sent in the range, by day and kind, and what
  became of their emails.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from statistics import median
from typing import Any, Literal

from sqlalchemy import ColumnElement, and_, func, select, true
from sqlalchemy.orm import Session

from app.cycles.service import ticket_code
from app.cycles.stages import STAGE_LABEL
from app.dashboard.figures import invoice_query
from app.models import (
    AlertKind,
    Assignment,
    Client,
    ClientStatus,
    Cycle,
    CycleAlert,
    CycleFiling,
    Email,
    EmailStatus,
    Filing,
    Role,
    Stage,
    StageChange,
    Ticket,
    User,
)
from app.timeutil import IST, ensure_utc
from app.worklist.buckets import NOT_FILED, Scope

Report = Literal[
    "filing-status", "late-filings", "fee-aging", "consultant-turnaround", "alert-delivery"
]
Cell = str | int | Decimal | date | None
FILED = (Stage.FILED, Stage.ACKNOWLEDGED, Stage.CLOSED)
NOT_STARTED = (Stage.SCHEDULED, Stage.AWAITING_DOCUMENTS, Stage.DOCUMENTS_RECEIVED)


@dataclass(frozen=True)
class Range:
    start: date
    end: date


@dataclass(frozen=True)
class Table:
    title: str
    columns: list[str]
    rows: list[list[Cell]]


def _owned(scope: Scope) -> ColumnElement[bool]:
    base = Client.status != ClientStatus.CLOSED
    return and_(base, Client.owner_id == scope.owner_id) if scope.owner_id is not None else base


def _owner(scope: Scope) -> ColumnElement[bool]:
    return Client.owner_id == scope.owner_id if scope.owner_id is not None else true()


def _rupees(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"))


def _filed_on(filing: CycleFiling | None) -> date | None:
    return ensure_utc(filing.filed_at).astimezone(IST).date() if filing else None


def filing_status(db: Session, scope: Scope, period: Range) -> Table:
    rows = db.execute(
        select(Cycle, Filing.code, CycleFiling)
        .join(Client, Client.id == Cycle.client_pk)
        .join(Filing, Filing.id == Cycle.filing_id)
        .outerjoin(CycleFiling, CycleFiling.cycle_id == Cycle.id)
        .where(_owned(scope), Cycle.due_date.between(period.start, period.end))
    ).all()
    groups: dict[tuple[str, str, date], list[int]] = defaultdict(lambda: [0] * 6)
    order: dict[tuple[str, str, date], date] = {}
    for cycle, code, filing in rows:
        key = (code, cycle.period_label, cycle.period_start)
        g = groups[key]
        order[key] = min(order.get(key, cycle.due_date), cycle.due_date)
        g[0] += 1
        filed_on = _filed_on(filing)
        if cycle.stage in FILED:
            g[3 if filed_on is not None and filed_on <= cycle.due_date else 4] += 1
        elif cycle.due_date < scope.on:
            g[5] += 1
        elif cycle.stage in NOT_STARTED:
            g[1] += 1
        else:
            g[2] += 1
    return Table(
        "Filing status by period",
        [
            "Filing",
            "Period",
            "Due",
            "Cycles",
            "Not started",
            "In progress",
            "Filed on time",
            "Filed late",
            "Overdue",
        ],
        [
            [code, label, order[(code, label, start)], *g]
            for (code, label, start), g in sorted(
                groups.items(), key=lambda kv: (kv[0][2], kv[0][0])
            )
        ],
    )


def late_filings(db: Session, scope: Scope, period: Range) -> Table:
    rows = db.execute(
        select(Cycle, Client, Ticket.number, Filing.code, CycleFiling)
        .join(Client, Client.id == Cycle.client_pk)
        .join(Ticket, Ticket.id == Cycle.ticket_id)
        .join(Filing, Filing.id == Cycle.filing_id)
        .outerjoin(CycleFiling, CycleFiling.cycle_id == Cycle.id)
        .where(_owned(scope), Cycle.due_date.between(period.start, period.end))
        .order_by(Cycle.due_date, Ticket.number)
    ).all()
    out: list[list[Cell]] = []
    for cycle, client, number, code, filing in rows:
        filed_on = _filed_on(filing)
        if filed_on is not None and filed_on > cycle.due_date:
            days, reason, status = (
                (filed_on - cycle.due_date).days,
                filing.late_reason,
                "Filed late",
            )
        elif filed_on is None and cycle.stage in NOT_FILED and cycle.due_date < scope.on:
            days, reason, status = (scope.on - cycle.due_date).days, None, "Not yet filed"
        else:
            continue
        out.append(
            [
                ticket_code(number),
                client.client_id,
                client.legal_name,
                code,
                cycle.period_label,
                cycle.due_date,
                filed_on,
                days,
                status,
                STAGE_LABEL[cycle.stage],
                reason,
            ]
        )
    return Table(
        "Late filings",
        [
            "Ticket",
            "Client ID",
            "Client",
            "Filing",
            "Period",
            "Due",
            "Filed on",
            "Days late",
            "Status",
            "Stage",
            "Reason",
        ],
        out,
    )


AGING = (
    (None, 0, "Not yet due"),
    (1, 30, "1-30 days"),
    (31, 60, "31-60 days"),
    (61, 90, "61-90 days"),
)


def fee_aging(db: Session, scope: Scope, period: Range) -> Table:
    del period  # as of today
    owed: dict[int, list[int]] = defaultdict(lambda: [0] * 5)
    names: dict[int, tuple[int, str, int]] = {}
    for figure in ("fees_due", "fees_overdue"):
        for invoice, client, balance in db.execute(invoice_query(scope, figure)).all():
            late = (scope.on - invoice.due_date).days
            bucket = (
                0 if late <= 0 else 1 if late <= 30 else 2 if late <= 60 else 3 if late <= 90 else 4
            )
            owed[client.id][bucket] += int(balance)
            names[client.id] = (client.client_id, client.legal_name, client.owner_id)
    owners = {
        u.id: u.full_name
        for u in db.scalars(select(User).where(User.id.in_({n[2] for n in names.values()})))
    }
    rows: list[list[Cell]] = []
    for pk, buckets in sorted(owed.items(), key=lambda kv: -sum(kv[1])):
        cid, name, owner = names[pk]
        rows.append(
            [cid, name, owners.get(owner), *(_rupees(b) for b in buckets), _rupees(sum(buckets))]
        )
    return Table(
        "Fee aging",
        [
            "Client ID",
            "Client",
            "Owner",
            "Not yet due",
            "1-30 days",
            "31-60 days",
            "61-90 days",
            "Over 90 days",
            "Total owed",
        ],
        rows,
    )


def consultant_turnaround(db: Session, scope: Scope, period: Range) -> Table:
    start = ensure_utc_day(period.start)
    end = ensure_utc_day(period.end, end=True)
    assignments = db.execute(
        select(Assignment, User)
        .join(User, User.id == Assignment.assignee_id)
        .join(Cycle, Cycle.id == Assignment.cycle_id)
        .join(Client, Client.id == Cycle.client_pk)
        .where(
            _owner(scope),
            Assignment.assigned_at >= start,
            Assignment.assigned_at < end,
        )
    ).all()
    sent: dict[int, list[Any]] = defaultdict(list)
    cycle_ids = {a.cycle_id for a, _ in assignments}
    if cycle_ids:
        for cycle_id, at in db.execute(
            select(StageChange.cycle_id, StageChange.changed_at).where(
                StageChange.cycle_id.in_(cycle_ids), StageChange.action == "submit_for_review"
            )
        ).all():
            sent[cycle_id].append(ensure_utc(at))
    per: dict[int, dict[str, Any]] = {}
    for a, user in assignments:
        p = per.setdefault(user.id, {"user": user, "assigned": 0, "done": [], "open": 0})
        p["assigned"] += 1
        assigned_at = ensure_utc(a.assigned_at)
        after = [t for t in sent.get(a.cycle_id, []) if t >= assigned_at]
        if after:
            p["done"].append((min(after) - assigned_at).total_seconds() / 86400)
        elif a.ended_at is None:
            p["open"] += 1
    role = {Role.CONSULTANT: "Consultant", Role.STAFF: "Staff", Role.ADMINISTRATOR: "Administrator"}
    rows: list[list[Cell]] = []
    for p in sorted(per.values(), key=lambda v: v["user"].full_name):
        done = p["done"]
        rows.append(
            [
                p["user"].full_name,
                role[p["user"].role],
                p["assigned"],
                len(done),
                Decimal(f"{sum(done) / len(done):.1f}") if done else None,
                Decimal(f"{median(done):.1f}") if done else None,
                Decimal(f"{max(done):.1f}") if done else None,
                p["open"],
            ]
        )
    return Table(
        "Consultant turnaround",
        [
            "Person",
            "Role",
            "Assigned",
            "Sent for review",
            "Average days",
            "Median days",
            "Longest days",
            "Still open",
        ],
        rows,
    )


def ensure_utc_day(day: date, *, end: bool = False) -> datetime:
    """The start of an India day (or of the next one), in UTC."""
    moment = datetime.combine(day + timedelta(days=1) if end else day, time(), tzinfo=IST)
    return moment.astimezone(UTC)


def alert_delivery(db: Session, scope: Scope, period: Range) -> Table:
    rows = db.execute(
        select(CycleAlert.sent_on, CycleAlert.kind, Email.status, func.count())
        .join(Email, Email.id == CycleAlert.email_id)
        .join(Cycle, Cycle.id == CycleAlert.cycle_id)
        .join(Client, Client.id == Cycle.client_pk)
        .where(
            _owner(scope),
            CycleAlert.sent_on.between(period.start, period.end),
        )
        .group_by(CycleAlert.sent_on, CycleAlert.kind, Email.status)
    ).all()
    table: dict[tuple[date, AlertKind], list[int]] = defaultdict(lambda: [0] * 4)
    for day, kind, status, n in rows:
        t = table[(day, kind)]
        t[0] += n
        if status is EmailStatus.DELIVERED:
            t[1] += n
        elif status in (EmailStatus.FAILED, EmailStatus.BOUNCED):
            t[2] += n
        else:
            t[3] += n
    label = {AlertKind.FIRST: "First alert", AlertKind.REMINDER: "Reminder"}
    return Table(
        "Alert delivery",
        ["Date", "Kind", "Sent", "Delivered", "Failed or bounced", "Sent, not yet confirmed"],
        [[day, label[kind], *t] for (day, kind), t in sorted(table.items())],
    )


REPORTS: dict[Report, Callable[[Session, Scope, Range], Table]] = {
    "filing-status": filing_status,
    "late-filings": late_filings,
    "fee-aging": fee_aging,
    "consultant-turnaround": consultant_turnaround,
    "alert-delivery": alert_delivery,
}
