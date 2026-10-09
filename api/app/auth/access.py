"""Record-level access: the Administrator sees all, Staff see what they own, Consultants only
what is assigned to them. Routes load the record, then call `authorize_record`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException

from app.auth.deps import Principal
from app.auth.events import security_event
from app.models import Role


@dataclass(frozen=True)
class RecordAccess:
    owner_id: int | None
    assignee_ids: frozenset[int] = field(default_factory=frozenset)


def authorize_record(principal: Principal, access: RecordAccess | None, *, what: str) -> None:
    """Raise unless the person may open this record. `None` means the record does not exist."""
    if access is None:
        raise HTTPException(status_code=404, detail="Not found.")
    if principal.role is Role.ADMINISTRATOR:
        return
    if principal.role is Role.STAFF and access.owner_id == principal.user_id:
        return
    if principal.role is Role.CONSULTANT and principal.user_id in access.assignee_ids:
        return
    security_event(
        "access.denied", user_id=principal.user_id, role=principal.role.value, record=what
    )
    if principal.role is Role.CONSULTANT:
        # Outsiders learn nothing, not even that the record exists.
        raise HTTPException(status_code=404, detail="Not found.")
    raise HTTPException(status_code=403, detail="You do not have access to this record.")
