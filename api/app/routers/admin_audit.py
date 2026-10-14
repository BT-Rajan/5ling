"""The audit log for the Administrator (BRD FR-AU-03, FR-AU-04): read, filter, check, export.

There is deliberately no route that changes or removes an entry (FR-AU-02).
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, aliased, sessionmaker

from app.audit.chain import timestamp
from app.audit.verify import PROBLEMS, run_check
from app.audit.writer import audit
from app.auth.deps import Principal, get_db, require_roles
from app.errors import ApiHTTPException
from app.models import AuditCheck, AuditEntry, CheckTrigger, Role, User
from app.timeutil import IST, format_ist, utcnow

router = APIRouter(prefix="/api/admin/audit", tags=["admin: audit log"])

admin_only = require_roles(Role.ADMINISTRATOR)

PAGE_MAX = 200
MAX_EXPORT_ROWS = 100_000
EXPORT_BATCH = 2000


class Filters(BaseModel):
    event: str | None = Field(default=None, min_length=1, max_length=64)
    actor_user_id: int | None = Field(default=None, ge=1)
    target_type: str | None = Field(default=None, min_length=1, max_length=40)
    target_id: str | None = Field(default=None, min_length=1, max_length=64)
    from_date: date | None = None  # India dates, both ends included
    to_date: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> Filters:
        if self.from_date and self.to_date and self.from_date > self.to_date:
            raise ValueError("The start date is after the end date.")
        return self

    def describe(self) -> str:
        parts = [f"{k}={v}" for k, v in self.model_dump(exclude_none=True).items()]
        return "; ".join(parts) or "none"


class EntryOut(BaseModel):
    seq: int
    occurred_at: datetime
    event: str
    actor_user_id: int | None
    actor_name: str | None
    actor_role: str | None
    ip: str | None
    request_id: str | None
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]
    entry_hash: str


class PageOut(BaseModel):
    items: list[EntryOut]
    next_before_seq: int | None  # pass back as before_seq for the next (older) page


class CheckOut(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime
    trigger: CheckTrigger
    requested_by_id: int | None
    ok: bool
    entries_checked: int
    last_seq: int
    first_bad_seq: int | None
    problem: str | None
    problem_text: str | None


class StatusOut(BaseModel):
    latest: CheckOut | None


def _check_out(check: AuditCheck) -> CheckOut:
    return CheckOut(
        id=check.id,
        started_at=check.started_at,
        finished_at=check.finished_at,
        trigger=check.trigger,
        requested_by_id=check.requested_by_id,
        ok=check.ok,
        entries_checked=check.entries_checked,
        last_seq=check.last_seq,
        first_bad_seq=check.first_bad_seq,
        problem=check.problem,
        problem_text=PROBLEMS.get(check.problem or ""),
    )


def _ist_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=IST).astimezone(UTC)


_Actor = aliased(User)


def _query(filters: Filters) -> Select[AuditEntry, str]:
    stmt = select(AuditEntry, _Actor.full_name).outerjoin(
        _Actor, _Actor.id == AuditEntry.actor_user_id
    )
    if filters.event:
        stmt = stmt.where(AuditEntry.event == filters.event)
    if filters.actor_user_id is not None:
        stmt = stmt.where(AuditEntry.actor_user_id == filters.actor_user_id)
    if filters.target_type:
        stmt = stmt.where(AuditEntry.target_type == filters.target_type)
    if filters.target_id:
        stmt = stmt.where(AuditEntry.target_id == filters.target_id)
    if filters.from_date:
        stmt = stmt.where(AuditEntry.occurred_at >= _ist_start(filters.from_date))
    if filters.to_date:
        stmt = stmt.where(AuditEntry.occurred_at < _ist_start(filters.to_date + timedelta(days=1)))
    return stmt


def _entry_out(entry: AuditEntry, actor_name: str | None) -> EntryOut:
    return EntryOut(
        seq=entry.seq,
        occurred_at=entry.occurred_at,
        event=entry.event,
        actor_user_id=entry.actor_user_id,
        actor_name=actor_name,
        actor_role=entry.actor_role,
        ip=entry.ip,
        request_id=entry.request_id,
        target_type=entry.target_type,
        target_id=entry.target_id,
        details=entry.details,
        entry_hash=entry.entry_hash,
    )


def _filters(
    event: str | None = Query(None, min_length=1, max_length=64),
    actor_user_id: int | None = Query(None, ge=1),
    target_type: str | None = Query(None, min_length=1, max_length=40),
    target_id: str | None = Query(None, min_length=1, max_length=64),
    from_date: date | None = None,
    to_date: date | None = None,
) -> Filters:
    try:
        return Filters(
            event=event,
            actor_user_id=actor_user_id,
            target_type=target_type,
            target_id=target_id,
            from_date=from_date,
            to_date=to_date,
        )
    except ValueError as exc:
        raise ApiHTTPException(422, "The start date is after the end date.") from exc


@router.get("/entries")
def list_entries(
    _: Principal = Depends(admin_only),  # noqa: B008  (first: refuse before validating)
    filters: Filters = Depends(_filters),  # noqa: B008
    before_seq: int | None = Query(None, ge=1),
    limit: int = Query(50, ge=1, le=PAGE_MAX),
    db: Session = Depends(get_db),  # noqa: B008
) -> PageOut:
    """Newest first. Page through with `before_seq`."""
    stmt = _query(filters)
    if before_seq is not None:
        stmt = stmt.where(AuditEntry.seq < before_seq)
    rows = db.execute(stmt.order_by(AuditEntry.seq.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    return PageOut(
        items=[_entry_out(entry, name) for entry, name in rows],
        next_before_seq=rows[-1][0].seq if more and rows else None,
    )


@router.get("/events")
def list_events(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[str]:
    """Every kind of event recorded so far, for the filter."""
    return list(db.scalars(select(AuditEntry.event).distinct().order_by(AuditEntry.event)))


@router.get("/status")
def status(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> StatusOut:
    latest = db.scalar(select(AuditCheck).order_by(AuditCheck.id.desc()).limit(1))
    return StatusOut(latest=_check_out(latest) if latest else None)


@router.get("/checks")
def list_checks(
    _: Principal = Depends(admin_only),  # noqa: B008
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),  # noqa: B008
) -> list[CheckOut]:
    checks = db.scalars(select(AuditCheck).order_by(AuditCheck.id.desc()).limit(limit))
    return [_check_out(c) for c in checks]


@router.post("/verify")
def verify_now(
    request: Request,
    actor: Principal = Depends(admin_only),  # noqa: B008
) -> CheckOut:
    factory: sessionmaker[Session] = request.app.state.session_factory
    check = run_check(factory, CheckTrigger.MANUAL, requested_by_id=actor.user_id)
    return _check_out(check)


# --- Export ------------------------------------------------------------------------------------

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value: object) -> str:
    """Text for one CSV cell. A leading formula character is neutralised for spreadsheets."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


EXPORT_COLUMNS = (
    "seq",
    "occurred_at_utc",
    "occurred_at_india",
    "event",
    "actor_user_id",
    "actor_name",
    "actor_role",
    "ip",
    "request_id",
    "target_type",
    "target_id",
    "details",
    "prev_hash",
    "entry_hash",
)


_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _comment(text: str) -> str:
    """One "# ..." line before the table. Control characters (a newline in a filter value)
    cannot start a line of their own."""
    return "# " + _CONTROL.sub(" ", text) + "\r\n"


def _csv_lines(rows: list[list[str]]) -> str:
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    return buffer.getvalue()


def _export_rows(
    factory: sessionmaker[Session], filters: Filters, up_to_seq: int, preamble: str
) -> Iterator[str]:
    yield preamble
    yield _csv_lines([list(EXPORT_COLUMNS)])
    after = 0
    with factory() as db:
        while True:
            batch = db.execute(
                _query(filters)
                .where(AuditEntry.seq > after, AuditEntry.seq <= up_to_seq)
                .order_by(AuditEntry.seq)
                .limit(EXPORT_BATCH)
            ).all()
            if not batch:
                return
            lines = [
                [
                    str(entry.seq),
                    # Exactly the timestamp the hash covers, so auditors can recompute it.
                    timestamp(entry.occurred_at),
                    format_ist(entry.occurred_at, "%Y-%m-%d %H:%M:%S"),
                    entry.event,
                    _cell(entry.actor_user_id),
                    _cell(name),
                    _cell(entry.actor_role),
                    _cell(entry.ip),
                    _cell(entry.request_id),
                    _cell(entry.target_type),
                    _cell(entry.target_id),
                    json.dumps(entry.details, sort_keys=True, ensure_ascii=False),
                    entry.prev_hash,
                    entry.entry_hash,
                ]
                for entry, name in batch
            ]
            after = batch[-1][0].seq
            db.expunge_all()
            yield _csv_lines(lines)


@router.post("/export")
def export(
    filters: Filters,
    request: Request,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> StreamingResponse:
    """CSV of the matching entries, oldest first, headed by a fresh chain check's result."""
    up_to_seq = int(db.scalar(select(func.coalesce(func.max(AuditEntry.seq), 0))) or 0)
    count = int(
        db.scalar(
            select(func.count()).select_from(
                _query(filters).where(AuditEntry.seq <= up_to_seq).subquery()
            )
        )
        or 0
    )
    if count > MAX_EXPORT_ROWS:
        raise ApiHTTPException(
            422,
            f"That is {count:,} entries; narrow the dates or filters to {MAX_EXPORT_ROWS:,} or fewer.",
            code="export_too_large",
        )
    db.rollback()

    factory: sessionmaker[Session] = request.app.state.session_factory
    check = run_check(factory, CheckTrigger.EXPORT, requested_by_id=actor.user_id)
    audit(
        db,
        "audit.exported",
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        details={
            "rows": count,
            "up_to_seq": up_to_seq,
            "check_id": check.id,
            "chain_ok": check.ok,
            **filters.model_dump(exclude_none=True, mode="json"),
        },
    )
    db.commit()

    now = utcnow()
    if check.ok:
        verdict = f"passed: entries 1 to {check.last_seq} verified, newest hash {check.last_hash}"
    else:
        where = f" at entry {check.first_bad_seq}" if check.first_bad_seq else ""
        verdict = f"FAILED{where}: {PROBLEMS.get(check.problem or '', check.problem)}"
    preamble = "".join(
        _comment(line)
        for line in (
            "Ledgerline audit log export",
            f"Generated: {timestamp(now)} (UTC) by user {actor.user_id}",
            f"Filters: {filters.describe()}",
            f"Chain check {check.id}: {verdict}",
            f"Rows: {count}",
        )
    )
    filename = f"ledgerline-audit-{now.astimezone(IST):%Y%m%d-%H%M}.csv"
    return StreamingResponse(
        _export_rows(factory, filters, up_to_seq, preamble),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Audit-Chain": "passed" if check.ok else "failed",
        },
    )
