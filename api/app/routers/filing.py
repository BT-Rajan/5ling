"""Marking a filing done, with its proof (BRD FR-WF-05).

Staff file returns on the government portals themselves, then record it here: the
acknowledgement file and the reference number the portal gave, in one request. The file is
stored like any other document (encrypted, scanned), the proof row is written, and the cycle
moves to Filed, all in one transaction. Without both there is no move: this route needs them,
the stage service refuses "file" without a proof row, and a database trigger refuses it too.
"""

from __future__ import annotations

import re
from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.cycles.service import MoveRefused, ticket_code, transition
from app.cycles.stages import STAGE_LABEL
from app.errors import ApiHTTPException
from app.files.documents import Infected, accept, forget_file
from app.files.types import FileRefused
from app.mail.updates import draft_updates
from app.models import Cycle, CycleFiling, Role, Stage, UploadedVia
from app.routers.documents import raw_body
from app.routers.tickets import TicketOut, TicketRef, _load, _ticket_out, can_edit

router = APIRouter(tags=["filing"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)

# Portal acknowledgement numbers and ARNs: letters, digits, and / or - inside.
REFERENCE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9/-]{3,48})[A-Za-z0-9]")


@router.post("/api/uploads/tickets/{ticket}/filing")
def record_filing(
    ticket: TicketRef,
    request: Request,
    x_file_name: Annotated[str, Header(max_length=600)],
    reference: Annotated[str, Query(max_length=60)],
    body: bytes = Depends(raw_body),
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> TicketOut:
    """Upload the acknowledgement with the reference number, and mark the cycle Filed."""
    settings = request.app.state.settings
    found, client = _load(db, principal, ticket, lock=True)
    if not can_edit(principal, client):
        raise ApiHTTPException(
            403,
            "Only the client's owner or an Administrator can mark this filed.",
            code="forbidden",
        )
    cycle = db.scalar(
        select(Cycle).where(Cycle.ticket_id == found.id).execution_options(populate_existing=True)
    )
    if cycle is None:
        raise ApiHTTPException(409, "This ticket has no filing stages.", code="not_a_cycle")
    if cycle.stage is not Stage.READY_TO_FILE:
        raise ApiHTTPException(
            409,
            f"This is in {STAGE_LABEL[cycle.stage]}; only work that is ready to file can be marked filed.",
            code="illegal_transition",
        )
    ref = reference.strip().upper()
    if not REFERENCE.fullmatch(ref):
        raise ApiHTTPException(
            422,
            "Enter the reference number exactly as the portal gave it: 5 to 50 letters and"
            " digits (/ and - allowed inside).",
            code="bad_reference",
        )
    store = request.app.state.store
    try:
        got = accept(
            db,
            store,
            request.app.state.scanner,
            client=client,
            cycle=cycle,
            name=unquote(x_file_name),
            data=body,
            max_bytes=settings.max_upload_bytes,
            title="Filing acknowledgement",
            document_id=None,
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
        message = (
            "A virus was found in this file, so it was not accepted." if infected else str(exc)
        )
        raise ApiHTTPException(
            422, message, code="infected" if infected else "file_refused"
        ) from None
    db.add(
        CycleFiling(
            cycle_id=cycle.id,
            reference=ref,
            acknowledgement_version_id=got.version.id,
            filed_by_id=principal.user_id,
        )
    )
    db.flush()  # the proof row first: the database refuses Filed without it
    try:
        transition(
            db,
            cycle,
            found,
            "file",
            actor_id=principal.user_id,
            actor_role=principal.role.value,
            expected_stage=Stage.READY_TO_FILE,
        )
    except MoveRefused as refused:  # the stage was checked under the lock, so not expected
        db.rollback()
        forget_file(store, got.version)
        raise ApiHTTPException(409, str(refused), code=refused.code) from None
    audit(
        db,
        "cycle.filed",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("ticket", ticket_code(found.number)),
        details={"reference": ref, "document_version": got.version.id},
    )
    draft_updates(db, settings.firm_name, client_pk=client.id)  # CR-03
    try:
        db.commit()
    except Exception:
        forget_file(store, got.version)
        raise
    return _ticket_out(db, found, client, principal)
