"""A cycle's document checklist (BRD FR-AL-04): what is asked for, and what has come in.

An item counts as received once one of its files has passed the virus check. Uploads against an
item collect in one document (created on the first upload, named after the item), so a second
upload is version 2 of the same thing (FR-AL-05).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.files.documents import Accepted, accept
from app.files.scan import Scanner
from app.files.store import EncryptedStore
from app.models import (
    Client,
    Cycle,
    CycleChecklistItem,
    DocumentVersion,
    ScanStatus,
    UploadedVia,
)

STATUS_LABEL = {
    "pending": "Still needed",
    "checking": "Received, being checked",
    "received": "Received",
}


@dataclass(frozen=True)
class FileSeen:
    version: int
    filename: str
    uploaded_at: datetime
    scan_status: ScanStatus


@dataclass(frozen=True)
class ItemView:
    item: CycleChecklistItem
    status: str  # pending, checking or received
    files: list[FileSeen]  # newest first

    @property
    def label(self) -> str:
        return STATUS_LABEL[self.status]


def items_for(db: Session, cycle_id: int) -> list[ItemView]:
    items = list(
        db.scalars(
            select(CycleChecklistItem)
            .where(CycleChecklistItem.cycle_id == cycle_id)
            .order_by(CycleChecklistItem.position)
        )
    )
    doc_ids = [i.document_id for i in items if i.document_id is not None]
    files: dict[int, list[FileSeen]] = {d: [] for d in doc_ids}
    if doc_ids:
        for v in db.scalars(
            select(DocumentVersion)
            .where(DocumentVersion.document_id.in_(doc_ids))
            .order_by(DocumentVersion.document_id, DocumentVersion.version.desc())
        ):
            files[v.document_id].append(
                FileSeen(v.version, v.filename, v.uploaded_at, v.scan_status)
            )
    views: list[ItemView] = []
    for item in items:
        seen = files.get(item.document_id, []) if item.document_id else []
        if any(f.scan_status is ScanStatus.CLEAN for f in seen):
            status = "received"
        elif any(f.scan_status is ScanStatus.PENDING for f in seen):
            status = "checking"
        else:
            status = "pending"
        views.append(ItemView(item=item, status=status, files=seen))
    return views


def upload_for_item(
    db: Session,
    store: EncryptedStore,
    scanner: Scanner,
    *,
    item_id: int,
    client: Client,
    cycle: Cycle,
    name: str,
    data: bytes,
    max_bytes: int,
    via: UploadedVia,
    uploaded_by_id: int | None = None,
    portal_link_id: int | None = None,
) -> Accepted | None:
    """Upload against one of this cycle's items. None if the item is not this cycle's.

    Raises FileRefused or Infected like `accept`. The item is locked, so two first uploads at
    once cannot start two documents for it.
    """
    item = db.scalar(
        select(CycleChecklistItem)
        .where(CycleChecklistItem.id == item_id, CycleChecklistItem.cycle_id == cycle.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        return None
    got = accept(
        db,
        store,
        scanner,
        client=client,
        cycle=cycle,
        name=name,
        data=data,
        max_bytes=max_bytes,
        title=item.label,
        document_id=item.document_id,
        via=via,
        uploaded_by_id=uploaded_by_id,
        portal_link_id=portal_link_id,
    )
    if item.document_id is None:
        item.document_id = got.document.id
    return got
