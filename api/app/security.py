"""Security middleware (pure ASGI): headers, request ids and logging, host check, body size limit.

Order, outermost first: SecurityHeaders > RequestContext > TrustedHost > BodyLimit > CORS.
SecurityHeaders is outermost so even error responses and rejected hosts carry the headers.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

log = logging.getLogger("ledgerline.http")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def error_body(code: str, message: str, request_id: str | None) -> bytes:
    return json.dumps(
        {"error": {"code": code, "message": message, "request_id": request_id}}
    ).encode()


async def send_error(
    send: Send, status: int, code: str, message: str, request_id: str | None = None
) -> None:
    body = error_body(code, message, request_id)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, production: bool, docs_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.production = production
        self.docs_paths = docs_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        skip_csp = (not self.production) and path.startswith(self.docs_paths) and self.docs_paths

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message["headers"] if k.lower() != b"server"]
                present = {k.lower() for k, _ in headers}

                def add(name: bytes, value: bytes) -> None:
                    if name not in present:
                        headers.append((name, value))

                add(b"x-content-type-options", b"nosniff")
                add(b"x-frame-options", b"DENY")
                add(b"referrer-policy", b"no-referrer")
                add(b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=()")
                add(b"cross-origin-opener-policy", b"same-origin")
                add(b"cross-origin-resource-policy", b"same-origin")
                add(b"cache-control", b"no-store")
                if not skip_csp:
                    add(
                        b"content-security-policy",
                        b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
                    )
                if self.production:
                    add(b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class RequestContextMiddleware:
    """Request id, one access-log line without the query string, and a generic 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = dict(scope.get("headers", [])).get(b"x-request-id", b"").decode("latin-1")
        request_id = inbound if _REQUEST_ID.match(inbound) else uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                message = {
                    **message,
                    "headers": [*message["headers"], (b"x-request-id", request_id.encode())],
                }
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            log.exception("unhandled error", extra={"ctx": {"request_id": request_id}})
            if not response_started:
                await send_error(
                    send_wrapper, 500, "internal_error", "Something went wrong.", request_id
                )
        finally:
            route = scope.get("route")
            log.info(
                "request",
                extra={
                    "ctx": {
                        "request_id": request_id,
                        "method": scope["method"],
                        "route": getattr(route, "path", "unmatched"),
                        "status": status,
                        "ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )


class TrustedHostMiddleware:
    def __init__(self, app: ASGIApp, *, allowed_hosts: list[str]) -> None:
        self.app = app
        self.allowed = {h.lower() for h in allowed_hosts}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            raw = dict(scope.get("headers", [])).get(b"host", b"").decode("latin-1").lower()
            host = raw.rsplit(":", 1)[0] if not raw.startswith("[") else raw.split("]")[0] + "]"
            if host not in self.allowed:
                if scope["type"] == "http":
                    rid = scope.get("state", {}).get("request_id")
                    await send_error(send, 400, "invalid_host", "Invalid host header.", rid)
                return
        await self.app(scope, receive, send)


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(
        self, app: ASGIApp, *, max_bytes: int, overrides: dict[str, int] | None = None
    ) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.overrides = overrides or {}  # path prefix -> limit, e.g. uploads (chunk C16)

    def _limit_for(self, path: str) -> int:
        for prefix, limit in self.overrides.items():
            if path.startswith(prefix):
                return limit
        return self.max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = self._limit_for(scope.get("path", ""))
        rid = scope.get("state", {}).get("request_id")
        declared = dict(scope.get("headers", [])).get(b"content-length")
        if declared is not None:
            try:
                too_big = int(declared) > limit
            except ValueError:
                too_big = True
            if too_big:
                await send_error(send, 413, "payload_too_large", "Request body is too large.", rid)
                return

        received = 0
        started = False
        exceeded = False

        async def receive_wrapper() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _TooLarge
            return message

        async def send_wrapper(message: Message) -> None:
            nonlocal started
            if exceeded:
                # The framework may turn our exception into its own 400. Replace whatever it
                # tries to send with a clear 413, and drop the rest of its response.
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await send_error(
                        send, 413, "payload_too_large", "Request body is too large.", rid
                    )
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        with contextlib.suppress(_TooLarge):
            await self.app(scope, receive_wrapper, send_wrapper)
        if exceeded and not started:
            started = True
            await send_error(send, 413, "payload_too_large", "Request body is too large.", rid)
