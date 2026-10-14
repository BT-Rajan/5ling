"""Security events (sign-in, denial, account changes): the redacted application log, plus the
tamper-evident audit trail (BRD FR-AU-01).

Pass `db=` when the caller commits that session right afterwards: the entry is then saved in the
same transaction as the change it describes. Without `db`, the entry is written at once in a
transaction of its own, which is what failures and refusals need (their caller's work is usually
being rolled back).

Context keys are mapped onto the trail's columns: `actor_id` (someone acting on `user_id`) or
`user_id` (someone acting on their own account), `role`, `ip`. Everything else goes into the
entry's details, redacted.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.audit import writer
from app.audit.chain import AuditRecord
from app.audit.context import current

log = logging.getLogger("ledgerline.security")

_WARN = {
    "login.failed",
    "login.throttled",
    "login.locked",
    "mfa.failed",
    "access.denied",
    "csrf.rejected",
    "origin.rejected",
    "request.denied",
    "review.approval_refused",
}


def _record(event: str, ctx: dict[str, Any]) -> AuditRecord:
    details = dict(ctx)
    actor_id = details.pop("actor_id", None)
    user_id = details.pop("user_id", None)
    ip = details.pop("ip", None)
    target: tuple[str, object] | None = None
    role: str | None = None
    if actor_id is not None:
        actor = actor_id
        if user_id is not None:
            target = ("user", user_id)
    else:
        actor = user_id
        role = details.pop("role", None)
    return writer.make_record(
        event,
        actor_user_id=int(actor) if actor is not None else None,
        actor_role=role,
        target=target,
        details=details,
        ip=ip,
    )


def security_event(event: str, *, db: Session | None = None, **ctx: Any) -> None:
    level = logging.WARNING if event in _WARN else logging.INFO
    log.log(level, "security event", extra={"ctx": {"event": event, **ctx}})

    record = _record(event, ctx)
    if db is not None:
        writer.queue(db, record)
        return
    request = current()
    sink = request.sink if request is not None else None
    if sink is None:
        # Outside a request (a script, a test) nothing says which database to use. Pass `db=`.
        log.error("audit entry not written: no database", extra={"ctx": {"event": event}})
        return
    try:
        writer.write_now(sink, [record])
    except Exception as exc:
        # The caller is already refusing or failing something; do not turn that into a 500.
        # Losing an entry is still serious, so it is logged loudly.
        log.error(
            "audit entry not written",
            extra={"ctx": {"event": event, "error": type(exc).__name__}},
        )
