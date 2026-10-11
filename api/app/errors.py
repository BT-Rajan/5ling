from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException


class ApiHTTPException(HTTPException):
    """An HTTP error with a stable machine-readable code the web app can act on."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        code: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=dict(headers or {}))
        self.code = code or f"http_{status_code}"
