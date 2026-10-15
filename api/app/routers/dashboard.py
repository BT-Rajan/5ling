"""The dashboard (BRD FR-RP-01, FR-RP-02).

Figures for the Administrator (every owner, or one) and for Staff (their own clients only,
whatever they ask for). Each figure opens its list with GET /api/dashboard/list, which runs the
very query the figure counted, so the list's total always equals the figure.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import Principal, get_db, require_roles
from app.catalog import rupees
from app.cycles.service import ticket_code
from app.cycles.stages import STAGE_LABEL
from app.dashboard.figures import (
    CYCLE_FIGURES,
    Figure,
    amount_of,
    count_of,
    cycle_query,
    invoice_query,
    stage_counts,
    workload_counts,
)
from app.errors import ApiHTTPException
from app.models import Cycle, Invoice, Role, Stage, Ticket, User
from app.routers.worklist import _scope
from app.timeutil import today_ist

router = APIRouter(tags=["dashboard"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)
LIST_LIMIT = 500


class StageCount(BaseModel):
    stage: Stage
    label: str
    count: int


class PersonCount(BaseModel):
    id: int
    full_name: str
    role: Role
    count: int


class Money(BaseModel):
    count: int
    amount: str


class DashboardOut(BaseModel):
    on: date
    owner_id: int | None
    stages: list[StageCount]
    overdue_filings: int
    documents_missing: int
    workload: list[PersonCount]  # most first
    fees_due: Money
    fees_overdue: Money


class CycleItem(BaseModel):
    code: str
    client_id: int
    client_name: str
    filing: str
    period_label: str
    due_date: date
    stage: Stage
    stage_label: str


class InvoiceItem(BaseModel):
    invoice_no: str
    client_id: int
    client_name: str
    period_label: str
    due_date: date
    total: str
    balance: str


class ListOut(BaseModel):
    figure: Figure
    key: str | None
    total: int  # equals the figure
    cycles: list[CycleItem]
    invoices: list[InvoiceItem]


@router.get("/api/dashboard")
def dashboard(
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    owner_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> DashboardOut:
    scope, _ = _scope(db, principal, request, owner_id, today_ist())
    loads = workload_counts(db, scope)
    people = {u.id: u for u in db.scalars(select(User).where(User.id.in_(list(loads))))}
    due, overdue = invoice_query(scope, "fees_due"), invoice_query(scope, "fees_overdue")
    return DashboardOut(
        on=scope.on,
        owner_id=scope.owner_id,
        stages=[
            StageCount(stage=s, label=STAGE_LABEL[s], count=n)
            for s, n in stage_counts(db, scope).items()
        ],
        overdue_filings=count_of(db, cycle_query(scope, "overdue_filings")),
        documents_missing=count_of(db, cycle_query(scope, "documents_missing")),
        workload=sorted(
            (
                PersonCount(id=p, full_name=people[p].full_name, role=people[p].role, count=n)
                for p, n in loads.items()
                if p in people
            ),
            key=lambda x: (-x.count, x.full_name),
        ),
        fees_due=Money(count=count_of(db, due), amount=rupees(amount_of(db, due)) or "0.00"),
        fees_overdue=Money(
            count=count_of(db, overdue), amount=rupees(amount_of(db, overdue)) or "0.00"
        ),
    )


@router.get("/api/dashboard/list")
def dashboard_list(
    request: Request,
    figure: Figure,
    key: str | None = Query(None, max_length=40),
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    owner_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),  # noqa: B008
) -> ListOut:
    """The list behind one figure: the same query, listed."""
    scope, _ = _scope(db, principal, request, owner_id, today_ist())
    cycles: list[CycleItem] = []
    invoices: list[InvoiceItem] = []
    if figure in CYCLE_FIGURES:
        try:
            query = cycle_query(scope, figure, key)
        except ValueError:
            raise ApiHTTPException(422, "Unknown figure.", code="bad_figure") from None
        total = count_of(db, query)
        for cycle, client, number, filing, _person in db.execute(
            query.order_by(Cycle.due_date, Ticket.number).limit(LIST_LIMIT)
        ).all():
            cycles.append(
                CycleItem(
                    code=ticket_code(number),
                    client_id=client.client_id,
                    client_name=client.legal_name,
                    filing=filing,
                    period_label=cycle.period_label,
                    due_date=cycle.due_date,
                    stage=cycle.stage,
                    stage_label=STAGE_LABEL[cycle.stage],
                )
            )
    else:
        query = invoice_query(scope, figure)
        total = count_of(db, query)
        for invoice, client, balance in db.execute(
            query.order_by(Invoice.due_date, Invoice.invoice_no).limit(LIST_LIMIT)
        ).all():
            invoices.append(
                InvoiceItem(
                    invoice_no=invoice.invoice_no,
                    client_id=client.client_id,
                    client_name=client.legal_name,
                    period_label=invoice.period_label,
                    due_date=invoice.due_date,
                    total=rupees(invoice.total_paise) or "0.00",
                    balance=rupees(int(balance)) or "0.00",
                )
            )
    return ListOut(figure=figure, key=key, total=total, cycles=cycles, invoices=invoices)
