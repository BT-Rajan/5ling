"""How an audit entry is hashed (BRD FR-AU-03). Chunk C04 verifies the trail with the same code.

entry_hash = SHA-256 of a canonical JSON document holding every stored field of the entry plus
the previous entry's hash. Changing, removing or reordering any entry breaks every hash after it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.timeutil import ensure_utc

FORMAT_VERSION = 1

JsonScalar = str | int | bool | None


@dataclass(frozen=True)
class AuditRecord:
    """One thing that happened, before it has a place in the chain."""

    occurred_at: datetime
    event: str
    actor_user_id: int | None = None
    actor_role: str | None = None
    ip: str | None = None
    request_id: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def canonical(document: dict[str, Any]) -> bytes:
    """Sorted keys, no spaces, UTF-8. No floats are ever stored (see `clean_details`)."""
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def timestamp(value: datetime) -> str:
    # DATETIME(6) keeps microseconds exactly, so this survives the round trip through MySQL.
    return ensure_utc(value).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def entry_hash(seq: int, prev_hash: str, record: AuditRecord) -> str:
    document = {
        "v": FORMAT_VERSION,
        "seq": seq,
        "prev": prev_hash,
        "at": timestamp(record.occurred_at),
        "event": record.event,
        "actor": record.actor_user_id,
        "role": record.actor_role,
        "ip": record.ip,
        "rid": record.request_id,
        "ttype": record.target_type,
        "tid": record.target_id,
        "details": record.details,
    }
    return hashlib.sha256(canonical(document)).hexdigest()
