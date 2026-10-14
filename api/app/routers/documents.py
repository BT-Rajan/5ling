"""Documents on a filing ticket, for staff (BRD FR-AL-04, FR-AL-05, NFR-SE-05, FR-AU-01).

Only the client's owner and the Administrator. Uploads are the raw file as the request body (no
multipart parser), at /api/uploads/..., the only place the larger size limit applies. Files are
never served from a public address: a download is a 5-minute link made for one person, checked
against their session when used, and every link made and every download is audited.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.access import RecordAccess, authorize_record
from app.auth.deps import Principal, get_db, require_roles
from app.config import Settings
from app.errors import ApiHTTPException
from app.files.checklist import items_for, upload_for_item
from app.files.documents import (
    Infected,
    accept,
    forget_file,
    make_link,
    read_link,
)
from app.files.store import EncryptedStore, StoreError
from app.files.types import FileRefused
from app.models import (
    Client,
    Cycle,
    Document,
    DocumentVersion,
    Role,
    ScanStatus,
    UploadedVia,
    User,
)
from app.routers.tickets import _load, can_edit

router = APIRouter(tags=["documents"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)

SCAN_LABEL = {
    ScanStatus.PENDING: "Waiting for the virus check",
    ScanStatus.CLEAN: "Checked",
    ScanStatus.INFECTED: "Removed: a virus was found",
}


class VersionOut(BaseModel):
    id: int
    version: int
    filename: str
    content_type: str
    size_bytes: int
    uploaded_via: UploadedVia
    uploaded_by: str | None  # None: the client, through their link
    uploaded_at: datetime
    scan_status: ScanStatus
    scan_label: str


class DocumentOut(BaseModel):
    id: int
    title: str
    latest_version: int
    versions: list[VersionOut]  # newest first


class LinkOut(BaseModel):
    url: str
    expires_at: datetime


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _cycle(db: Session, principal: Principal, ref: str) -> tuple[Client, Cycle]:
    ticket, client = _load(db, principal, ref)
    if not can_edit(principal, client):
        raise ApiHTTPException(403, "Only the client's owner can do this.", code="forbidden")
    cycle = db.scalar(select(Cycle).where(Cycle.ticket_id == ticket.id))
    if cycle is None:
        raise ApiHTTPException(409, "Task tickets have no documents.", code="not_a_cycle")
    return client, cycle


def documents_of(db: Session, cycle_id: int) -> list[DocumentOut]:
    docs = list(
        db.scalars(select(Document).where(Document.cycle_id == cycle_id).order_by(Document.id))
    )
    if not docs:
        return []
    rows = db.execute(
        select(DocumentVersion, User.full_name)
        .outerjoin(User, User.id == DocumentVersion.uploaded_by_id)
        .where(DocumentVersion.document_id.in_([d.id for d in docs]))
        .order_by(DocumentVersion.document_id, DocumentVersion.version.desc())
    ).all()
    versions: dict[int, list[VersionOut]] = {d.id: [] for d in docs}
    for v, name in rows:
        versions[v.document_id].append(
            VersionOut(
                id=v.id,
                version=v.version,
                filename=v.filename,
                content_type=v.content_type,
                size_bytes=v.size_bytes,
                uploaded_via=v.uploaded_via,
                uploaded_by=name,
                uploaded_at=v.uploaded_at,
                scan_status=v.scan_status,
                scan_label=SCAN_LABEL[v.scan_status],
            )
        )
    return [
        DocumentOut(
            id=d.id, title=d.title, latest_version=d.latest_version, versions=versions[d.id]
        )
        for d in docs
    ]


class ItemOut(BaseModel):
    id: int
    label: str
    required: bool
    status: str  # pending, checking, received
    status_label: str
    document_id: int | None


@router.get("/api/tickets/{ticket}/checklist")
def checklist(
    ticket: str,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[ItemOut]:
    """FR-AL-04: what the client has been asked for on this filing, and what has come in."""
    _, cycle = _cycle(db, principal, ticket)
    return [
        ItemOut(
            id=v.item.id,
            label=v.item.label,
            required=v.item.required,
            status=v.status,
            status_label=v.label,
            document_id=v.item.document_id,
        )
        for v in items_for(db, cycle.id)
    ]


@router.get("/api/tickets/{ticket}/documents")
def list_documents(
    ticket: str,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[DocumentOut]:
    """FR-AL-05: each document with its version history: who uploaded what, and when."""
    _, cycle = _cycle(db, principal, ticket)
    return documents_of(db, cycle.id)


async def raw_body(request: Request) -> bytes:
    return await request.body()


@router.post("/api/uploads/tickets/{ticket}", status_code=201)
def upload(
    ticket: str,
    request: Request,
    x_file_name: Annotated[str, Header(max_length=600)],
    title: Annotated[str | None, Query(max_length=200)] = None,
    document_id: Annotated[int | None, Query(ge=1)] = None,
    item_id: Annotated[int | None, Query(ge=1)] = None,
    body: bytes = Depends(raw_body),
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[DocumentOut]:
    """Upload a file: for a checklist item (`item_id`), a new document (`title`), or a new
    version of one (`document_id`).

    The body is the file itself; its name comes percent-encoded in the X-File-Name header.
    """
    settings = _settings(request)
    store: EncryptedStore = request.app.state.store
    client, cycle = _cycle(db, principal, ticket)
    name = unquote(x_file_name)
    try:
        if item_id is not None:
            found = upload_for_item(
                db,
                store,
                request.app.state.scanner,
                item_id=item_id,
                client=client,
                cycle=cycle,
                name=name,
                data=body,
                max_bytes=settings.max_upload_bytes,
                via=UploadedVia.STAFF,
                uploaded_by_id=principal.user_id,
            )
            if found is None:
                raise FileRefused("That checklist item is not on this filing.")
            got = found
        else:
            got = accept(
                db,
                store,
                request.app.state.scanner,
                client=client,
                cycle=cycle,
                name=name,
                data=body,
                max_bytes=settings.max_upload_bytes,
                title=title,
                document_id=document_id,
                via=UploadedVia.STAFF,
                uploaded_by_id=principal.user_id,
            )
    except (FileRefused, Infected) as exc:
        db.rollback()
        infected = isinstance(exc, Infected)
        audit(
            db,
            "document.infected" if infected else "document.rejected",
            actor_user_id=principal.user_id,
            actor_role=principal.role.value,
            target=("cycle", cycle.id),
            details={
                "reason": (exc.signature or "malware") if isinstance(exc, Infected) else str(exc),
                "size": len(body),
            },
        )
        db.commit()
        if infected:
            raise ApiHTTPException(
                422,
                "A virus was found in this file, so it was not accepted.",
                code="infected",
            ) from None
        raise ApiHTTPException(422, str(exc), code="file_refused") from None
    audit(
        db,
        "document.uploaded",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("document_version", got.version.id),
        details={
            "document": got.document.id,
            "version": got.version.version,
            "size": got.version.size_bytes,
            "scan": got.version.scan_status,
        },
    )
    try:
        db.commit()
    except Exception:
        forget_file(store, got.version)
        raise
    return documents_of(db, cycle.id)


@router.post("/api/documents/versions/{version_id}/link")
def download_link(
    version_id: int,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> LinkOut:
    """A 5-minute download link for one checked version, for this person only."""
    version, _ = _version(db, principal, version_id)
    if version.scan_status is not ScanStatus.CLEAN:
        raise ApiHTTPException(409, SCAN_LABEL[version.scan_status] + ".", code="not_available")
    token = make_link(_settings(request), version.id, principal.user_id)
    audit(
        db,
        "document.viewed",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("document_version", version.id),
    )
    db.commit()
    expires = datetime.fromtimestamp(int(token.split(".")[2]), tz=UTC)
    return LinkOut(url=f"/api/files/{token}", expires_at=expires)


def _version(db: Session, principal: Principal, version_id: int) -> tuple[DocumentVersion, Client]:
    version = db.get(DocumentVersion, version_id)
    document = db.get(Document, version.document_id) if version else None
    client = db.get(Client, document.client_pk) if document else None
    authorize_record(
        principal,
        RecordAccess(owner_id=client.owner_id) if client and version else None,
        what="document",
    )
    if version is None or client is None:  # authorize_record raised already
        raise ApiHTTPException(404, "Not found.", code="not_found")
    return version, client


@router.get("/api/files/{token}")
def download(
    token: str,
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    """Serve a file through a link made in the last 5 minutes for this same person."""
    found = read_link(_settings(request), token)
    if found is None or found[1] != principal.user_id:
        raise ApiHTTPException(
            410, "This download link has expired. Open the document again.", code="link_expired"
        )
    version, _ = _version(db, principal, found[0])  # access is checked again, now
    if version.scan_status is not ScanStatus.CLEAN:
        raise ApiHTTPException(409, SCAN_LABEL[version.scan_status] + ".", code="not_available")
    store: EncryptedStore = request.app.state.store
    try:
        data = store.get(version.storage_key)
    except StoreError:
        raise ApiHTTPException(500, "The file could not be read.", code="file_unreadable") from None
    audit(
        db,
        "document.downloaded",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("document_version", version.id),
    )
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
