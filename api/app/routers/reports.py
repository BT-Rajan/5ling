"""Reports and exports (BRD FR-RP-03, FR-RP-04).

A report is shown as JSON (GET) and exported (POST, so it carries the CSRF token: an export
writes to the audit log) as a spreadsheet (XLSX) or PDF. Both use the same scope
as the screens: the Administrator sees everyone (or one owner), Staff only their own clients,
whatever they ask for. Every export is written to the audit log with who, which report, the
filters and how many rows it held; looking at a report on screen is not an export.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.errors import ApiHTTPException
from app.models import Role
from app.reports.definitions import REPORTS, Cell, Range, Report, Table
from app.reports.files import pdf, xlsx
from app.routers.worklist import _scope
from app.timeutil import format_ist, today_ist, utcnow

router = APIRouter(tags=["reports"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
SHOW_LIMIT = 2000  # rows on screen; exports carry everything up to EXPORT_LIMIT
EXPORT_LIMIT = 50_000
MAX_DAYS = 731

Value = str | int | None


class ReportOut(BaseModel):
    report: Report
    title: str
    start: date
    end: date
    owner_id: int | None
    columns: list[str]
    rows: list[list[Value]]
    total: int
    truncated: bool


def _range(start: date | None, end: date | None) -> Range:
    today = today_ist()
    first = (today.replace(day=1) - timedelta(days=60)).replace(day=1)
    period = Range(start or first, end or today + timedelta(days=31))
    if period.start > period.end:
        raise ApiHTTPException(422, "The start date is after the end date.", code="bad_range")
    if (period.end - period.start).days > MAX_DAYS:
        raise ApiHTTPException(422, "Choose a range of two years or less.", code="bad_range")
    return period


def _value(cell: Cell) -> Value:
    if isinstance(cell, Decimal):
        return f"{cell:.2f}" if cell.as_tuple().exponent == -2 else str(cell)
    if isinstance(cell, date):
        return cell.isoformat()
    return cell


def _run(
    db: Session,
    principal: Principal,
    request: Request,
    report: Report,
    owner_id: int | None,
    start: date | None,
    end: date | None,
) -> tuple[Table, Range, int | None]:
    scope, _ = _scope(db, principal, request, owner_id, today_ist())
    period = _range(start, end)
    return REPORTS[report](db, scope, period), period, scope.owner_id


@router.get("/api/reports/{report}")
def show_report(
    report: Report,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    owner_id: int | None = Query(None, ge=1),
    start: date | None = Query(None, alias="from"),  # noqa: B008
    end: date | None = Query(None, alias="to"),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ReportOut:
    table, period, owner = _run(db, principal, request, report, owner_id, start, end)
    return ReportOut(
        report=report,
        title=table.title,
        start=period.start,
        end=period.end,
        owner_id=owner,
        columns=table.columns,
        rows=[[_value(c) for c in row] for row in table.rows[:SHOW_LIMIT]],
        total=len(table.rows),
        truncated=len(table.rows) > SHOW_LIMIT,
    )


class ExportIn(BaseModel):
    format: Literal["xlsx", "pdf"]
    owner_id: int | None = Field(default=None, ge=1)
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")


@router.post("/api/reports/{report}/export")
def export_report(
    report: Report,
    request: Request,
    body: ExportIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """FR-RP-04: the same rows as on screen, as a file, and an audit entry for it."""
    format = body.format
    table, period, owner = _run(db, principal, request, report, body.owner_id, body.start, body.end)
    if len(table.rows) > EXPORT_LIMIT:
        raise ApiHTTPException(
            422, "That is too many rows to export. Choose a shorter range.", code="too_many_rows"
        )
    audit(
        db,
        "report.exported",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("report", report),
        details={
            "format": format,
            "from": period.start.isoformat(),
            "to": period.end.isoformat(),
            "owner_id": owner,
            "rows": len(table.rows),
        },
    )
    db.commit()
    name = f"ledgerline-{report}-{period.start.isoformat()}-to-{period.end.isoformat()}"
    if format == "xlsx":
        content = xlsx(table.title, table.columns, table.rows)
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        scope_text = "all owners" if owner is None else f"owner #{owner}"
        subtitle = (
            f"{period.start.isoformat()} to {period.end.isoformat()}, {scope_text}; "
            f"exported {format_ist(utcnow(), '%d %b %Y %H:%M IST')}"
        )
        content = pdf(table.title, subtitle, table.columns, table.rows)
        media = "application/pdf"
    return Response(
        content=content,
        media_type=media,
        headers={
            "Content-Disposition": f'attachment; filename="{name}.{format}"',
            "Cache-Control": "no-store",
        },
    )
