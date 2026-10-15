"""The editable rules (BRD NFR-MN-01): one row, read where each rule is used."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RuleSettings


def rules(db: Session, *, lock: bool = False) -> RuleSettings:
    stmt = select(RuleSettings).where(RuleSettings.id == 1)
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:  # loaded by migration 0027 and never deleted (trigger)
        raise RuntimeError("rule settings row missing")
    return row
