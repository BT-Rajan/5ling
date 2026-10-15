from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import Engine

from app.audit.context import AuditContextMiddleware
from app.auth.sessions import cookie_name
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.files.scan import ClamdScanner
from app.files.store import store_from_settings
from app.logging_conf import setup_logging
from app.routers import (
    admin_audit,
    admin_emails,
    admin_extensions,
    admin_filings,
    admin_jobs,
    admin_packages,
    admin_rules,
    admin_schedule,
    admin_users,
    assignments,
    auth,
    auth_consultant,
    client_updates,
    clients,
    consultant,
    dashboard,
    documents,
    email_events,
    fees,
    filing,
    health,
    notifications,
    portal,
    portal_uploads,
    reports,
    ticket_links,
    tickets,
    worklist,
)
from app.security import (
    BodyLimitMiddleware,
    OriginCheckMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
    TrustedHostMiddleware,
)

DOCS_PATHS = ("/api/docs", "/api/openapi.json")


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    engine = engine or make_engine(settings.database_url.get_secret_value())
    # Audit entries that must outlive the request's own transaction (refusals, failed sign-ins)
    # use a separate small pool, so a request never waits for a second connection from the pool
    # it already holds one of.
    audit_engine = make_engine(
        settings.database_url.get_secret_value(), pool_size=5, max_overflow=5
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()
        audit_engine.dispose()

    app = FastAPI(
        title="Ledgerline API",
        docs_url=None if settings.is_production else "/api/docs",
        openapi_url=None if settings.is_production else "/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = make_session_factory(engine)
    app.state.store = store_from_settings(settings)  # documents, encrypted (C16)
    app.state.scanner = ClamdScanner(settings.clamd_address)

    def rid(request: Request) -> str | None:
        return getattr(request.state, "request_id", None)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": getattr(exc, "code", f"http_{exc.status_code}"),
                    "message": str(exc.detail),
                    "request_id": rid(request),
                    **getattr(exc, "extra", {}),
                }
            },
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Deliberately omit the submitted values: they can contain PAN, GSTIN or other identifiers.
        problems = [
            {"where": list(e["loc"]), "problem": e["msg"], "type": e["type"]} for e in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Some fields are not valid.",
                    "fields": problems,
                    "request_id": rid(request),
                }
            },
            status_code=422,
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(auth_consultant.router)
    app.include_router(admin_users.router)
    app.include_router(admin_audit.router)
    app.include_router(admin_filings.router)
    app.include_router(admin_jobs.router)
    app.include_router(admin_extensions.router)
    app.include_router(admin_emails.router)
    app.include_router(email_events.router)
    app.include_router(ticket_links.router)
    app.include_router(portal.router)
    app.include_router(client_updates.router)
    app.include_router(documents.router)
    app.include_router(filing.router)
    app.include_router(fees.router)
    app.include_router(dashboard.router)
    app.include_router(portal_uploads.router)
    app.include_router(notifications.router)
    app.include_router(worklist.router)
    app.include_router(assignments.router)
    app.include_router(consultant.router)
    app.include_router(admin_packages.router)
    app.include_router(admin_schedule.router)
    app.include_router(clients.router)
    app.include_router(tickets.router)
    app.include_router(reports.router)
    app.include_router(admin_rules.router)

    # add_middleware puts the newest outermost; the first one added is closest to the app.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-Request-Id"],
        max_age=600,
    )
    app.add_middleware(
        BodyLimitMiddleware,
        max_bytes=settings.max_body_bytes,
        # File uploads, and nothing else, may be larger (FR-AL-04).
        overrides={"/api/uploads/": settings.max_upload_bytes + 1024},
    )
    app.add_middleware(OriginCheckMiddleware, allowed_origins=settings.allowed_origins)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        AuditContextMiddleware,
        sink=make_session_factory(audit_engine),
        cookie=cookie_name(settings),
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        SecurityHeadersMiddleware, production=settings.is_production, docs_paths=DOCS_PATHS
    )
    return app


def app_factory() -> FastAPI:
    """Entry point for uvicorn: `uvicorn app.main:app_factory --factory`."""
    return create_app()
