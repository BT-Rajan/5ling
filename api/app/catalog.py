"""Shared pieces for the package and calendar endpoints: checklists, money, versions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CalendarVersion, PackageVersion

MAX_CHECKLIST = 30

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class ChecklistEntry(BaseModel):
    label: Label
    required: bool = True


def unique_labels(items: list[ChecklistEntry]) -> list[ChecklistEntry]:
    seen: set[str] = set()
    for item in items:
        key = " ".join(item.label.lower().split())
        if key in seen:
            raise ValueError(f"'{item.label}' is listed twice.")
        seen.add(key)
    return items


def paise(amount: Decimal) -> int:
    return int((amount * 100).to_integral_value())


def rupees(value: int | None) -> str | None:
    """Paise as a decimal string, e.g. 1500050 -> "15000.50" (no float rounding anywhere)."""
    if value is None:
        return None
    return f"{value // 100}.{value % 100:02d}"


def next_version[V: (CalendarVersion, PackageVersion)](
    db: Session, model: type[V], owner_column: Any, owner_id: int
) -> int:
    """The number for a new version. Call with the owner row locked, so two cannot collide."""
    current = db.scalar(select(func.max(model.version)).where(owner_column == owner_id))
    return int(current or 0) + 1


def status_of(published_at: datetime | None) -> str:
    return "published" if published_at is not None else "draft"
