"""The client's checklist and uploads in the portal (BRD FR-AL-04, FR-AL-05).

Everything here needs a live portal session (the cookie a private link starts), and only ever
reaches that session's one cycle: an item of any other cycle is "not found". Uploads also need
the session's CSRF token. A refused file comes back with its reason, for the client to read, and
the refusal is recorded in the audit log.
"""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import get_db
from app.config import Settings
from app.errors import ApiHTTPException
from app.files.checklist import ItemView, items_for, upload_for_item
from app.files.documents import Infected, forget_file
from app.files.types import ALLOWED_TEXT, FileRefused
from app.models import Client, Cycle, PortalLink, ScanStatus, UploadedVia
from app.portal.links import LinkProblem, cookie_name, csrf_for, load_session
from app.timeutil import utcnow

router = APIRouter(tags=["client portal"])


class FileOut(BaseModel):
    version: int
    filename: str
    uploaded_at: datetime
    checked: bool


class ItemOut(BaseModel):
    id: int
    label: str
    required: bool
    status: str  # pending, checking, received
    status_label: str
    files: list[FileOut]


class ChecklistOut(BaseModel):
    items: list[ItemOut]
    still_needed: int  # required items not yet received
    allowed: str  # the kinds of file accepted, in words
    max_mb: int


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _session(request: Request, db: Session) -> tuple[PortalLink, Client, Cycle, str]:
    token = request.cookies.get(cookie_name(_settings(request)))
    try:
        link, client, cycle = load_session(db, token)
    except LinkProblem as exc:
        raise ApiHTTPException(
            exc.status, str(exc), code=exc.code, extra={"can_request_new": exc.can_request_new}
        ) from None
    return link, client, cycle, token or ""


def checklist_out(settings: Settings, views: list[ItemView]) -> ChecklistOut:
    return ChecklistOut(
        items=[
            ItemOut(
                id=v.item.id,
                label=v.item.label,
                required=v.item.required,
                status=v.status,
                status_label=v.label,
                # The client sees their own files' names, never anything to open or download.
                files=[
                    FileOut(
                        version=f.version,
                        filename=f.filename,
                        uploaded_at=f.uploaded_at,
                        checked=f.scan_status is ScanStatus.CLEAN,
                    )
                    for f in v.files
                    if f.scan_status is not ScanStatus.INFECTED
                ],
            )
            for v in views
        ],
        still_needed=sum(1 for v in views if v.item.required and v.status != "received"),
        allowed=ALLOWED_TEXT,
        max_mb=settings.max_upload_bytes // (1024 * 1024),
    )


@router.get("/api/portal/checklist")
def portal_checklist(request: Request, db: Session = Depends(get_db)) -> ChecklistOut:  # noqa: B008
    """FR-AL-04: what is still needed for this filing, and what has come in."""
    link, _, cycle, _ = _session(request, db)
    link.last_seen_at = utcnow()
    db.commit()
    return checklist_out(_settings(request), items_for(db, cycle.id))


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/api/uploads/portal/items/{item_id}", status_code=201)
def portal_upload(
    item_id: int,
    request: Request,
    x_file_name: Annotated[str, Header(max_length=600)],
    body: bytes = Depends(raw_body),
    db: Session = Depends(get_db),  # noqa: B008
) -> ChecklistOut:
    """Upload one file against one checklist item. Refused files say why (FR-AL-04)."""
    settings = _settings(request)
    link, client, cycle, token = _session(request, db)
    sent = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(sent.encode(), csrf_for(settings, token).encode()):
        raise ApiHTTPException(403, "This request could not be verified.", code="csrf_rejected")
    store = request.app.state.store
    try:
        got = upload_for_item(
            db,
            store,
            request.app.state.scanner,
            item_id=item_id,
            client=client,
            cycle=cycle,
            name=unquote(x_file_name),
            data=body,
            max_bytes=settings.max_upload_bytes,
            via=UploadedVia.PORTAL,
            portal_link_id=link.id,
        )
    except (FileRefused, Infected) as exc:
        db.rollback()
        infected = isinstance(exc, Infected)
        reason = (
            "A virus was found in this file, so it was not accepted. Please check your computer "
            "and send a clean copy."
            if infected
            else str(exc)
        )
        audit(
            db,
            "document.infected" if infected else "document.rejected",
            actor_role="client",
            target=("portal_link", link.id),
            details={
                "item": item_id,
                "client_id": client.client_id,
                "reason": (exc.signature or "malware") if isinstance(exc, Infected) else str(exc),
                "size": len(body),
            },
        )
        db.commit()
        raise ApiHTTPException(
            422, reason, code="infected" if infected else "file_refused"
        ) from None
    if got is None:
        raise ApiHTTPException(404, "Not found.", code="not_found")
    audit(
        db,
        "document.uploaded",
        actor_role="client",
        target=("document_version", got.version.id),
        details={
            "via": "portal",
            "portal_link": link.id,
            "item": item_id,
            "version": got.version.version,
            "size": got.version.size_bytes,
            "scan": got.version.scan_status,
        },
    )
    link.last_seen_at = utcnow()
    try:
        db.commit()
    except Exception:
        forget_file(store, got.version)
        raise
    return checklist_out(settings, items_for(db, cycle.id))
