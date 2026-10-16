"""Importing clients from a spreadsheet (FR-CL-05, chunk C33).

Two steps, so nothing is saved before the person has seen the report:

1. `POST /api/clients/import/check` with the CSV file as the body. Every row is checked as the
   client form would check it (see `app/client_import.py`), and nothing is saved. The answer lists
   each row with problems and its reasons, how many rows are ready, and a confirmation token.
2. `POST /api/clients/import/save?token=...` with the same file. The token proves this person
   checked this exact file (its SHA-256) in the last 30 minutes. The file is checked again, since
   Ledgerline may have changed meanwhile. Then the rows without problems are saved, each in its
   own transaction, through the same code as the client form, and audited. Rows with problems are
   left out and listed. Saving the same file twice adds nothing twice: saved rows are duplicates
   the second time.

Staff import clients they own (FR-AC-03). An Administrator names each row's owner.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import client_import
from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.generator import run_quietly
from app.errors import ApiHTTPException
from app.models import JobTrigger, Role, User
from app.routers.clients import save_client

router = APIRouter(prefix="/api/clients/import", tags=["clients: import"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)

MAX_ROWS = 2000
TOKEN_SECONDS = 30 * 60


class RowProblem(BaseModel):
    line: int
    source_id: str
    problems: list[str]


class CheckOut(BaseModel):
    rows: int
    ready: int
    problems: list[RowProblem]
    token: str  # send back with the same file to save the ready rows


class SavedRow(BaseModel):
    line: int
    source_id: str
    client_id: int


class SaveOut(BaseModel):
    saved: list[SavedRow]
    skipped: list[RowProblem]


def _token_key(request: Request) -> bytes:
    secret = request.app.state.settings.secret_key.get_secret_value().encode()
    return hmac.new(secret, b"ledgerline client import token", hashlib.sha256).digest()


def _sign(key: bytes, user_id: int, digest: str, expires: int) -> str:
    message = f"{user_id}:{digest}:{expires}".encode()
    return f"{expires}.{hmac.new(key, message, hashlib.sha256).hexdigest()}"


def _token_ok(key: bytes, token: str, user_id: int, digest: str) -> bool:
    expires_text, _, _ = token.partition(".")
    if not expires_text.isdigit() or int(expires_text) < time.time():
        return False
    return hmac.compare_digest(token, _sign(key, user_id, digest, int(expires_text)))


async def _file(request: Request) -> tuple[bytes, str]:
    """The file (the body limit middleware has already refused anything too large)."""
    data = await request.body()
    return data, hashlib.sha256(data).hexdigest()


def _limit(request: Request) -> int:
    return int(request.app.state.settings.max_body_bytes)


def _rows(
    db: Session, principal: Principal, data: bytes, max_bytes: int
) -> list[client_import.Row]:
    try:
        raw = client_import.parse(
            data,
            max_bytes=max_bytes,
            max_rows=MAX_ROWS,
            required=client_import.REQUIRED
            + (("owner_email",) if principal.role is Role.ADMINISTRATOR else ()),
        )
    except client_import.ImportFileError as exc:
        raise ApiHTTPException(422, str(exc), code="bad_import_file") from exc
    staff = db.get(User, principal.user_id) if principal.role is Role.STAFF else None
    rows = client_import.check(db, raw, staff_owner=staff)
    for row in rows:  # here, unlike a migration re-run, an existing client is a problem
        if len(row.existing) == 1:
            row.problems.append(f"client {next(iter(row.existing))} already has this PAN or GSTIN.")
    return rows


def _problems(rows: list[client_import.Row]) -> list[RowProblem]:
    return [
        RowProblem(line=r.line, source_id=r.source_id, problems=r.problems)
        for r in rows
        if r.problems
    ]


@router.post("/check")
async def check_import(
    request: Request,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> CheckOut:
    data, digest = await _file(request)
    rows = await run_in_threadpool(_check, db, principal, data, digest, _limit(request))
    expires = int(time.time()) + TOKEN_SECONDS
    return CheckOut(
        rows=len(rows),
        ready=sum(1 for r in rows if not r.problems),
        problems=_problems(rows),
        token=_sign(_token_key(request), principal.user_id, digest, expires),
    )


def _check(
    db: Session, principal: Principal, data: bytes, digest: str, max_bytes: int
) -> list[client_import.Row]:
    rows = _rows(db, principal, data, max_bytes)
    audit(
        db,
        "client.import_checked",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        details={
            "rows": len(rows),
            "ready": sum(1 for r in rows if not r.problems),
            "file_sha256": digest,
        },
    )
    db.commit()
    return rows


@router.post("/save")
async def save_import(
    request: Request,
    token: Annotated[str, Query(min_length=10, max_length=100)],
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> SaveOut:
    data, digest = await _file(request)
    if not _token_ok(_token_key(request), token, principal.user_id, digest):
        raise ApiHTTPException(
            409,
            "Check this file first: it has changed, or the check is more than 30 minutes old.",
            code="check_first",
        )
    saved, rows = await run_in_threadpool(_save, db, principal, data, digest, _limit(request))
    made = [pk for pk, _ in saved]
    if made:  # FR-CY-01: their cycles appear with them
        await run_in_threadpool(
            run_quietly,
            request.app.state.session_factory,
            JobTrigger.CLIENT_CREATED,
            client_pks=made,
            requested_by_id=principal.user_id,
        )
    return SaveOut(saved=[row for _, row in saved], skipped=_problems(rows))


def _save(
    db: Session, principal: Principal, data: bytes, digest: str, max_bytes: int
) -> tuple[list[tuple[int, SavedRow]], list[client_import.Row]]:
    rows = _rows(db, principal, data, max_bytes)
    saved: list[tuple[int, SavedRow]] = []
    for row in rows:
        if row.problems or row.body is None:
            continue
        try:
            client = save_client(db, principal, row.body)
            db.commit()
        except ApiHTTPException as exc:  # e.g. saved by someone else a moment ago
            db.rollback()
            row.problems.append(str(exc.detail))
            continue
        saved.append(
            (
                client.id,
                SavedRow(line=row.line, source_id=row.source_id, client_id=client.client_id),
            )
        )
    audit(
        db,
        "client.imported",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        details={"rows": len(rows), "saved": len(saved), "file_sha256": digest},
    )
    db.commit()
    return saved, rows
