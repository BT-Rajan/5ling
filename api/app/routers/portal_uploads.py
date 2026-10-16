"""The client's checklist and uploads in the portal (BRD FR-AL-04, FR-AL-05).

Everything here needs a live portal session (the cookie a private link starts), and only ever
reaches that session's one cycle: an item of any other cycle is "not found". Uploads also need
the session's CSRF token. A refused file comes back with its reason, for the client to read, and
the refusal is recorded in the audit log.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import get_db
from app.config import Settings
from app.cycles.service import ticket_code
from app.errors import ApiHTTPException
from app.files.checklist import ItemView, items_for, upload_for_item
from app.files.documents import Infected, forget_file
from app.files.store import StoreError
from app.files.types import ALLOWED_TEXT, FileRefused
from app.followup import FollowUpRefused, confirm_no_activity
from app.models import (
    Client,
    ClientMessage,
    Cycle,
    CycleFiling,
    DocumentVersion,
    Filing,
    NilConfirmation,
    NotificationKind,
    PortalLink,
    ScanStatus,
    Stage,
    Ticket,
    UploadedVia,
)
from app.notify.service import COLLECTING, documents_arrived, notify
from app.portal.links import LinkProblem, cookie_name, csrf_for, load_session, portal_finished
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
    # FR-CY-03: the client may say the period had no activity, while documents are still awaited.
    can_confirm_no_activity: bool = False
    no_activity_confirmed_at: datetime | None = None
    # FR-WF-06: once filed, the client may download the acknowledgement; uploads are closed.
    finished: bool = False
    acknowledgement: AcknowledgementOut | None = None
    # FR-AL-07: the client's own messages about this filing, oldest first.
    messages: list[MessageOut] = []
    can_message: bool = False


class AcknowledgementOut(BaseModel):
    reference: str
    filename: str
    filed_at: datetime


class MessageOut(BaseModel):
    id: int
    body: str
    sent_at: datetime


class MessageIn(BaseModel):
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


MESSAGES_PER_DAY = 20


class NoActivityIn(BaseModel):
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None


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


def checklist_out(
    settings: Settings, views: list[ItemView], cycle: Cycle | None = None, db: Session | None = None
) -> ChecklistOut:
    confirmed = (
        db.scalar(select(NilConfirmation.confirmed_at).where(NilConfirmation.cycle_id == cycle.id))
        if cycle is not None and db is not None
        else None
    )
    received = any(v.status != "pending" for v in views)
    filed = (
        db.execute(
            select(CycleFiling.reference, DocumentVersion.filename, CycleFiling.filed_at)
            .join(DocumentVersion, DocumentVersion.id == CycleFiling.acknowledgement_version_id)
            .where(CycleFiling.cycle_id == cycle.id)
        ).first()
        if cycle is not None and db is not None
        else None
    )
    messages = (
        [
            MessageOut(id=m.id, body=m.body, sent_at=m.created_at)
            for m in db.scalars(
                select(ClientMessage)
                .where(ClientMessage.cycle_id == cycle.id)
                .order_by(ClientMessage.id)
            )
        ]
        if cycle is not None and db is not None
        else []
    )
    return ChecklistOut(
        finished=cycle is not None and portal_finished(cycle),
        acknowledgement=AcknowledgementOut(reference=filed[0], filename=filed[1], filed_at=filed[2])
        if filed
        else None,
        messages=messages,
        can_message=cycle is not None and cycle.stage is not Stage.CLOSED,
        can_confirm_no_activity=cycle is not None
        and confirmed is None
        and cycle.stage in COLLECTING
        and not received,
        no_activity_confirmed_at=confirmed,
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
    return checklist_out(_settings(request), items_for(db, cycle.id), cycle, db)


@router.post("/api/portal/no-activity")
def portal_no_activity(
    body: NoActivityIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> ChecklistOut:
    """FR-CY-03: the client confirms nothing happened this period; the cycle goes to review."""
    settings = _settings(request)
    link, _, cycle, token = _session(request, db)
    _verify(request, settings, token)
    if any(v.status != "pending" for v in items_for(db, cycle.id)):
        raise ApiHTTPException(
            409,
            "You have already sent documents for this period, so we will work from those.",
            code="documents_sent",
        )
    try:
        confirm_no_activity(db, link, cycle.id, body.note, firm_name=settings.firm_name)
    except FollowUpRefused as exc:
        db.rollback()
        raise ApiHTTPException(409, exc.message, code=exc.code) from None
    link.last_seen_at = utcnow()
    db.commit()
    db.refresh(cycle)
    return checklist_out(settings, items_for(db, cycle.id), cycle, db)


@router.post("/api/portal/messages", status_code=201)
def portal_message(
    body: MessageIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> ChecklistOut:
    """FR-AL-07: the client writes to the firm; kept against the cycle, and the owner is told."""
    settings = _settings(request)
    link, client, cycle, token = _session(request, db)
    _verify(request, settings, token)
    if cycle.stage is Stage.CLOSED:
        raise ApiHTTPException(
            409, "This filing is closed. Please email us instead.", code="closed"
        )
    since = utcnow() - timedelta(days=1)
    recent = db.scalar(
        select(func.count())
        .select_from(ClientMessage)
        .where(ClientMessage.cycle_id == cycle.id, ClientMessage.created_at >= since)
    )
    if (recent or 0) >= MESSAGES_PER_DAY:
        raise ApiHTTPException(
            429, "That is a lot of messages for one day. Please call us instead.", code="too_many"
        )
    message = ClientMessage(
        cycle_id=cycle.id,
        client_pk=client.id,
        portal_link_id=link.id,
        body=body.body,
        created_at=utcnow(),
    )
    db.add(message)
    db.flush()
    ticket = db.get(Ticket, cycle.ticket_id)
    filing = db.get(Filing, cycle.filing_id)
    code = ticket_code(ticket.number) if ticket else ""
    what = f"{client.legal_name}, {filing.code if filing else ''} {cycle.period_label}"
    notify(
        db,
        kind=NotificationKind.CLIENT_MESSAGE,
        dedupe_key=f"message:{message.id}",
        client_pk=client.id,
        cycle_id=cycle.id,
        user_id=client.owner_id,
        title=f"Message from the client: {what}",
        body=f"On {code}:\n" + body.body[:1500],
    )
    # The audit keeps that a message came, not what it says (it may hold personal details).
    audit(
        db,
        "portal.message_received",
        actor_role="client",
        target=("ticket", code),
        details={"portal_link": link.id, "length": len(body.body)},
    )
    link.last_seen_at = utcnow()
    db.commit()
    return checklist_out(settings, items_for(db, cycle.id), cycle, db)


@router.get("/api/portal/acknowledgement")
def portal_acknowledgement(request: Request, db: Session = Depends(get_db)) -> Response:  # noqa: B008
    """FR-WF-06: the filed acknowledgement, for this link's client and cycle only."""
    link, _, cycle, _ = _session(request, db)
    version = db.scalar(
        select(DocumentVersion)
        .join(CycleFiling, CycleFiling.acknowledgement_version_id == DocumentVersion.id)
        .where(CycleFiling.cycle_id == cycle.id)
    )
    if version is None or version.scan_status is not ScanStatus.CLEAN:
        raise ApiHTTPException(
            404, "There is no acknowledgement for this filing yet.", code="not_found"
        )
    try:
        data = request.app.state.store.get(version.storage_key)
    except StoreError:
        raise ApiHTTPException(500, "The file could not be read.", code="file_unreadable") from None
    audit(
        db,
        "document.downloaded",
        actor_role="client",
        target=("document_version", version.id),
        details={"via": "portal", "portal_link": link.id},
    )
    link.last_seen_at = utcnow()
    db.commit()
    return Response(
        content=data,
        media_type=version.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(version.filename)}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


def _verify(request: Request, settings: Settings, token: str) -> None:
    """The portal's CSRF check: the token derived from this session (state-changing calls)."""
    sent = request.headers.get("x-csrf-token", "")
    if not hmac.compare_digest(sent.encode(), csrf_for(settings, token).encode()):
        raise ApiHTTPException(403, "This request could not be verified.", code="csrf_rejected")


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
    _verify(request, settings, token)
    if portal_finished(cycle):
        raise ApiHTTPException(
            409, "This filing is done, so nothing more can be uploaded.", code="finished"
        )
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
    documents_arrived(db, cycle.id, firm_name=settings.firm_name)  # FR-IN-01
    try:
        db.commit()
    except Exception:
        forget_file(store, got.version)
        raise
    return checklist_out(settings, items_for(db, cycle.id), cycle, db)
