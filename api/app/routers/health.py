from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text

from app.models import AppMeta

router = APIRouter(prefix="/api/health", tags=["health"])
log = logging.getLogger("ledgerline.health")


@router.get("/live")
def live() -> dict[str, str]:
    """Process is up. No dependencies are checked here."""
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request) -> JSONResponse:
    """Database reachable and migrated. Failure details go to the log, never to the caller."""
    checks = {"database": "fail"}
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
            baseline = session.scalar(select(AppMeta.value).where(AppMeta.key == "baseline"))
            checks["database"] = "ok" if baseline else "not_migrated"
    except Exception:
        log.exception("readiness check failed")
    healthy = checks["database"] == "ok"
    return JSONResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status_code=200 if healthy else 503,
    )
