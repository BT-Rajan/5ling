"""Call the app's own API in process, as a named Administrator (C32).

Migration and catalog loading go through the same routes the screens use, so every row gets the
same validation, duplicate checks, cycle generation and audit entries as one typed in by hand. No
HTTP client library is needed: requests are handed straight to the ASGI app.

Running these tools needs a shell on the server, which already allows `create-admin`. The session
opened here is the named Administrator's own; it is signed out when the tool finishes.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import MutableMapping
from types import TracebackType
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import select

from app.auth.sessions import cookie_name, create_session
from app.config import Settings
from app.db import make_engine, make_session_factory
from app.main import create_app
from app.models import Role, User


class ApiError(Exception):
    def __init__(self, status: int, body: Any) -> None:
        self.status, self.body = status, body
        super().__init__(f"{status}: {self.message}")

    @property
    def code(self) -> str | None:
        error = self.body.get("error") if isinstance(self.body, dict) else None
        return error.get("code") if isinstance(error, dict) else None

    @property
    def message(self) -> str:
        error = self.body.get("error") if isinstance(self.body, dict) else None
        if isinstance(error, dict):
            return str(error.get("message") or error.get("code") or error)
        return str(self.body)[:300]


class Api:
    """`with Api(settings, "boss@firm.in") as api: api.post("/api/clients", {...})`"""

    def __init__(self, settings: Settings, admin_email: str) -> None:
        self.settings = settings
        self.admin_email = admin_email.strip().lower()
        self._loop = asyncio.new_event_loop()
        self._engine = make_engine(settings.database_url.get_secret_value())
        self.factory = make_session_factory(self._engine)
        self._app = create_app(settings, self._engine)
        self._headers: dict[str, str] = {}
        self.user_id = 0

    def __enter__(self) -> Api:
        try:
            self._sign_in()
        except BaseException:
            self._loop.close()
            self._engine.dispose()
            raise
        return self

    def _sign_in(self) -> None:
        with self.factory() as db:
            user = db.scalar(select(User).where(User.email == self.admin_email))
            if user is None or not user.is_active or user.role != Role.ADMINISTRATOR:
                raise SystemExit(f"{self.admin_email} is not an active Administrator.")
            token, row = create_session(db, user, "127.0.0.1")
            db.commit()
            self.user_id = user.id
            self._headers = {
                "cookie": f"{cookie_name(self.settings)}={token}",
                "x-csrf-token": row.csrf_token,
                "user-agent": "ledgerline-cli",
            }

    def __exit__(
        self, kind: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        try:
            if self._headers:
                self._request("POST", "/api/auth/logout", None, {})
        finally:
            self._loop.close()
            self._engine.dispose()

    def get(self, path: str, **query: Any) -> Any:
        return self._ok(*self._request("GET", path, None, query))

    def post(self, path: str, body: Any = None, **query: Any) -> Any:
        return self._ok(*self._request("POST", path, body, query))

    def put(self, path: str, body: Any) -> Any:
        return self._ok(*self._request("PUT", path, body, {}))

    @staticmethod
    def _ok(status: int, body: Any) -> Any:
        if status >= 400:
            raise ApiError(status, body)
        return body

    def _request(self, method: str, path: str, body: Any, query: dict[str, Any]) -> tuple[int, Any]:
        data = b"" if body is None else json.dumps(body).encode()
        headers = {**self._headers, "host": self.settings.allowed_hosts[0]}
        if body is not None:
            headers["content-type"] = "application/json"
            headers["content-length"] = str(len(data))
        status, raw = self._loop.run_until_complete(self._call(method, path, query, data, headers))
        try:
            return status, json.loads(raw) if raw else None
        except ValueError:
            return status, raw.decode("utf-8", "replace")

    async def _call(
        self, method: str, path: str, query: dict[str, Any], data: bytes, headers: dict[str, str]
    ) -> tuple[int, bytes]:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": urlencode(query).encode(),
            "root_path": "",
            "headers": [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()],
            "client": ("127.0.0.1", 0),
            "server": (headers["host"], 443),
        }
        sent = False
        status = 500
        chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": data, "more_body": False}

        async def send(message: MutableMapping[str, Any]) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await self._app(scope, receive, send)
        return status, b"".join(chunks)
