"""Background jobs, seen and re-run by the Administrator (BRD FR-CY-05)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.auth.deps import Principal, get_db, require_roles
from app.cycles.generator import JOB_NAME, run
from app.models import JobRun, JobStatus, JobTrigger, Role

router = APIRouter(prefix="/api/admin/jobs", tags=["admin: jobs"])

admin_only = require_roles(Role.ADMINISTRATOR)


class RunOut(BaseModel):
    id: int
    trigger: JobTrigger
    status: JobStatus
    started_at: datetime
    finished_at: datetime | None
    clients_checked: int
    clients_failed: int
    cycles_created: int
    error: str | None


class GeneratorOut(BaseModel):
    # The newest finished run that was not skipped: a failure here is shown on every screen.
    latest: RunOut | None
    recent: list[RunOut]


def _out(r: JobRun) -> RunOut:
    return RunOut.model_validate(r, from_attributes=True)


@router.get("/cycle-generator")
def generator_status(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> GeneratorOut:
    recent = list(
        db.scalars(
            select(JobRun).where(JobRun.job == JOB_NAME).order_by(JobRun.id.desc()).limit(10)
        )
    )
    latest = next(
        (r for r in recent if r.status in (JobStatus.OK, JobStatus.FAILED)),
        None,
    )
    return GeneratorOut(latest=_out(latest) if latest else None, recent=[_out(r) for r in recent])


@router.post("/cycle-generator/run")
def run_generator(
    request: Request,
    actor: Principal = Depends(admin_only),  # noqa: B008
) -> RunOut:
    """FR-CY-05: run it again. Safe at any time: it only makes cycles that are missing."""
    factory: sessionmaker[Session] = request.app.state.session_factory
    return _out(run(factory, JobTrigger.MANUAL, requested_by_id=actor.user_id))
