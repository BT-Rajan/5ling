"""Accepting, scanning and handing out document files (BRD FR-AL-04, FR-AL-05, NFR-SE-05).

Upload order, so that nothing unsafe is ever stored or shown:
1. the type and size are checked (refused with a reason);
2. the bytes are scanned in memory; an infected file is refused and never stored;
3. if the scanner cannot be reached, the file is stored but held ("pending") until a later scan;
4. the file is encrypted and written under a new random name; the version row is added under a
   lock on its document, so two uploads at once get versions 2 and 3, never two 2s.

Downloads go through short-lived signed links (5 minutes) that also name the person they were
made for, so a copied link is useless to anyone else and soon to its owner.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit import audit
from app.config import Settings
from app.files.scan import Scanner, ScannerUnavailable
from app.files.store import EncryptedStore, StoreError, new_key
from app.files.types import FileRefused, check
from app.models import (
    Client,
    Cycle,
    Document,
    DocumentVersion,
    ScanStatus,
    UploadedVia,
)
from app.timeutil import utcnow

log = logging.getLogger("ledgerline.files")

LINK_SECONDS = 300
MAX_TITLE = 200


class Infected(Exception):
    def __init__(self, signature: str | None) -> None:
        super().__init__(signature or "malware")
        self.signature = signature


@dataclass
class Accepted:
    document: Document
    version: DocumentVersion


def _title(title: str) -> str:
    title = " ".join(title.split())
    if not title or len(title) > MAX_TITLE:
        raise FileRefused(f"Give the document a name of up to {MAX_TITLE} characters.")
    return title


def accept(
    db: Session,
    store: EncryptedStore,
    scanner: Scanner,
    *,
    client: Client,
    cycle: Cycle,
    name: str,
    data: bytes,
    max_bytes: int,
    title: str | None = None,
    document_id: int | None = None,
    via: UploadedVia = UploadedVia.STAFF,
    uploaded_by_id: int | None = None,
    portal_link_id: int | None = None,
) -> Accepted:
    """Check, scan, store and record one upload. Raises FileRefused or Infected.

    The caller audits and commits. `document_id` adds a version to that document (of this cycle);
    otherwise a new document called `title` is started.
    """
    filename, kind = check(name, data, max_bytes)
    try:
        verdict = scanner.scan(data)
    except ScannerUnavailable as exc:
        log.warning(
            "virus scanner unavailable; file held until scanned", extra={"ctx": {"error": str(exc)}}
        )
        status = ScanStatus.PENDING
    else:
        if not verdict.clean:
            raise Infected(verdict.signature)
        status = ScanStatus.CLEAN

    if document_id is not None:
        document = db.scalar(
            select(Document)
            .where(Document.id == document_id, Document.cycle_id == cycle.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if document is None:
            raise FileRefused("That document is not on this filing.")
        document.latest_version += 1
    else:
        document = Document(
            client_pk=client.id,
            cycle_id=cycle.id,
            title=_title(title or filename.rpartition(".")[0] or filename),
            latest_version=1,
            created_by_id=uploaded_by_id,
            created_at=utcnow(),
        )
        db.add(document)
    db.flush()

    key = new_key()
    now = utcnow()
    version = DocumentVersion(
        document_id=document.id,
        version=document.latest_version,
        storage_key=key,
        filename=filename,
        content_type=kind.content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        uploaded_via=via,
        uploaded_by_id=uploaded_by_id,
        portal_link_id=portal_link_id,
        uploaded_at=now,
        scan_status=status,
        scanned_at=now if status is ScanStatus.CLEAN else None,
    )
    db.add(version)
    db.flush()  # the row exists (and the version number is ours) before the file is written
    store.put(key, data)
    return Accepted(document=document, version=version)


def forget_file(store: EncryptedStore, version: DocumentVersion) -> None:
    """After a failed commit: the stored ciphertext has no row, so remove it."""
    try:
        store.delete(version.storage_key)
    except (StoreError, OSError):
        log.exception("could not remove an orphaned stored file")


@dataclass
class ScanTally:
    clean: int = 0
    infected: int = 0
    waiting: int = 0
    errors: list[str] = field(default_factory=list)


def scan_pending(
    factory: sessionmaker[Session], store: EncryptedStore, scanner: Scanner, *, batch: int = 50
) -> ScanTally:
    """Scan files that were held because the scanner was down (INT-03). Run by the worker."""
    tally = ScanTally()
    with factory() as db:
        ids = list(
            db.scalars(
                select(DocumentVersion.id)
                .where(DocumentVersion.scan_status == ScanStatus.PENDING)
                .order_by(DocumentVersion.id)
                .limit(batch)
            )
        )
    for version_id in ids:
        with factory() as db:
            version = db.scalar(
                select(DocumentVersion)
                .where(DocumentVersion.id == version_id)
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
            if version is None or version.scan_status is not ScanStatus.PENDING:
                continue
            try:
                verdict = scanner.scan(store.get(version.storage_key))
            except ScannerUnavailable:
                tally.waiting += 1
                break  # try again next round
            except StoreError as exc:
                tally.errors.append(f"version {version.id}: {exc}")
                log.error("held file could not be read", extra={"ctx": {"version": version.id}})
                continue
            version.scanned_at = utcnow()
            if verdict.clean:
                version.scan_status = ScanStatus.CLEAN
                tally.clean += 1
            else:
                version.scan_status = ScanStatus.INFECTED
                version.scan_detail = (verdict.signature or "malware")[:150]
                tally.infected += 1
                audit(
                    db,
                    "document.infected",
                    target=("document_version", version.id),
                    details={"signature": version.scan_detail, "found": "later scan"},
                )
                log.warning("held file was infected", extra={"ctx": {"version": version.id}})
            db.commit()
            if not verdict.clean:
                store.delete(version.storage_key)  # the record stays; the file goes
    return tally


def _link_key(settings: Settings) -> bytes:
    return hmac.new(
        settings.secret_key.get_secret_value().encode(),
        b"ledgerline/download-link/v1",
        hashlib.sha256,
    ).digest()


def make_link(settings: Settings, version_id: int, user_id: int, now: float | None = None) -> str:
    expires = int((now or time.time()) + LINK_SECONDS)
    body = f"{version_id}.{user_id}.{expires}"
    sig = hmac.new(_link_key(settings), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def read_link(settings: Settings, token: str, now: float | None = None) -> tuple[int, int] | None:
    """(version id, user id) if the link is genuine and not expired; otherwise None."""
    parts = token.split(".")
    if len(parts) != 4 or not all(p.isdigit() for p in parts[:3]) or len(token) > 200:
        return None
    body = ".".join(parts[:3])
    good = hmac.new(_link_key(settings), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(good, parts[3]):
        return None
    if int(parts[2]) < (now or time.time()):
        return None
    return int(parts[0]), int(parts[1])
