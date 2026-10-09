"""Security events (sign-in, denial, account changes).

For now these go to the redacted application log. Chunk C03 routes the same calls into the
tamper-evident audit log, so callers never need to change.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("ledgerline.security")

_WARN = {
    "login.failed",
    "login.throttled",
    "login.locked",
    "access.denied",
    "csrf.rejected",
    "origin.rejected",
}


def security_event(event: str, **ctx: Any) -> None:
    level = logging.WARNING if event in _WARN else logging.INFO
    log.log(level, "security event", extra={"ctx": {"event": event, **ctx}})
