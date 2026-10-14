"""Delivery reports from the email provider (BRD INT-01, FR-AL-06): delivered, bounced, failed.

Not signed in: the caller is the provider (or a small relay that turns its webhook format into
this one). Every request must be signed with LL_EMAIL_WEBHOOK_SECRET:

    X-Ledgerline-Timestamp: <unix seconds>
    X-Ledgerline-Signature: sha256=<hex HMAC-SHA256 of "<timestamp>." + the raw body>

A request more than 5 minutes old or new is refused, so a captured one cannot be replayed later.
With no secret configured the endpoint does not exist (404).

Body: {"events": [{"message_id": "<...@domain>" or "email_id": "<32 hex>",
                   "event": "delivered" | "bounced" | "failed", "detail": "..."}]}
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy.orm import Session

from app.auth.deps import get_db
from app.errors import ApiHTTPException
from app.mail.outbox import REPORTABLE, apply_report

router = APIRouter(prefix="/api/email", tags=["email delivery reports"])
log = logging.getLogger("ledgerline.mail")

MAX_SKEW_SECONDS = 300
_PUBLIC_ID = re.compile(r"^[0-9a-f]{32}$")
_IN_MESSAGE_ID = re.compile(r"\.([0-9a-f]{32})@")  # make_msgid puts our ID before the "@"
Detail = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


class Report(BaseModel):
    email_id: str | None = Field(default=None, max_length=32)
    message_id: str | None = Field(default=None, max_length=250)
    event: Literal["delivered", "bounced", "failed"]
    detail: Detail | None = None

    @model_validator(mode="after")
    def _one_id(self) -> Report:
        if self.public_id() is None:
            raise ValueError("give email_id or a message_id this system sent")
        return self

    def public_id(self) -> str | None:
        if self.email_id is not None:
            return self.email_id if _PUBLIC_ID.match(self.email_id) else None
        if self.message_id is not None:
            found = _IN_MESSAGE_ID.search(self.message_id)
            return found.group(1) if found else None
        return None


class Reports(BaseModel):
    events: list[Report] = Field(min_length=1, max_length=100)


class ResultOut(BaseModel):
    applied: int
    ignored: int


async def raw_body(request: Request) -> bytes:
    return await request.body()


def _verify(request: Request, body: bytes) -> None:
    secret = request.app.state.settings.email_webhook_secret
    if secret is None:
        raise ApiHTTPException(404, "Not found.", code="not_found")
    stamp = request.headers.get("x-ledgerline-timestamp", "")
    sent = request.headers.get("x-ledgerline-signature", "")
    try:
        age = abs(time.time() - int(stamp))
    except ValueError:
        age = float("inf")
    expected = hmac.new(
        secret.get_secret_value().encode(), stamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    # Compare in constant time, and always compare (even when the timestamp is already wrong).
    good = hmac.compare_digest(f"sha256={expected}".encode(), sent.encode())
    if not good or age > MAX_SKEW_SECONDS:
        log.warning("email delivery report refused", extra={"ctx": {"stale": good}})
        raise ApiHTTPException(401, "Signature missing, wrong or too old.", code="bad_signature")


@router.post("/events")
def delivery_reports(
    request: Request,
    body: bytes = Depends(raw_body),
    db: Session = Depends(get_db),  # noqa: B008
) -> ResultOut:
    _verify(request, body)
    try:
        reports = Reports.model_validate_json(body)
    except ValidationError:
        raise ApiHTTPException(422, "Not a valid report.", code="bad_report") from None
    applied = ignored = 0
    for report in reports.events:
        public_id = report.public_id()
        changed = (
            apply_report(db, public_id, REPORTABLE[report.event], report.detail)
            if public_id
            else None
        )
        if changed is None:
            ignored += 1
        else:
            applied += 1
    db.commit()
    return ResultOut(applied=applied, ignored=ignored)
