"""Per-request audit context, and the middleware that records denied requests (BRD FR-AU-01).

The middleware makes the request id, the caller's address and the audit database reachable from
anywhere in the request (sync endpoints run in a worker thread; context variables follow them
there). After the response is sent it records any 401 or 403 that no more specific audit entry
already explained, so every refusal leaves a trace even when the code refusing wrote none.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import anyio

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from app.security import ASGIApp, Message, Receive, Scope, Send


@dataclass
class RequestAudit:
    request_id: str | None
    ip: str | None
    sink: sessionmaker[Session] | None  # where entries outside the caller's transaction go
    recorded: bool = False  # set when anything was audited during this request


_current: ContextVar[RequestAudit | None] = ContextVar("ll_request_audit", default=None)


def current() -> RequestAudit | None:
    return _current.get()


def deny_reason(scope: MutableMapping[str, Any], event: str) -> None:
    """Let an inner middleware name the event for a refusal it sends itself."""
    scope.setdefault("state", {})["audit_deny_event"] = event


class AuditContextMiddleware:
    def __init__(self, app: ASGIApp, *, sink: sessionmaker[Session], cookie: str) -> None:
        self.app = app
        self.sink = sink
        self.cookie = cookie.encode("latin-1")

    def _had_session_cookie(self, scope: Scope) -> bool:
        raw = dict(scope.get("headers", [])).get(b"cookie", b"")
        return any(
            part.strip().split(b"=", 1)[0] == self.cookie for part in raw.split(b";") if part
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ctx = RequestAudit(
            request_id=scope.get("state", {}).get("request_id"),
            ip=client[0] if client else None,
            sink=self.sink,
        )
        token = _current.set(ctx)
        status = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            _current.reset(token)

        # A signed-out visitor getting 401 is routine (the web app asks "who am I?" on load);
        # a 401 for someone holding a session cookie, and every 403, is worth a record.
        if ctx.recorded or not (
            status == 403 or (status == 401 and self._had_session_cookie(scope))
        ):
            return
        from app.auth.events import security_event  # here to avoid an import cycle

        route = scope.get("route")
        event = scope.get("state", {}).get("audit_deny_event", "request.denied")
        method = scope.get("method")
        path = getattr(route, "path", "unmatched")

        def write() -> None:
            token = _current.set(ctx)
            try:
                security_event(event, method=method, route=path, status=status)
            finally:
                _current.reset(token)

        # The response has gone; the database write happens off the event loop.
        await anyio.to_thread.run_sync(write)
