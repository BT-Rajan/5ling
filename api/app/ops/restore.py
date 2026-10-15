"""Checks on a restored backup (BRD NFR-AV-02), run by scripts/restore-test against the scratch
database it loaded: the schema is the latest, the audit log's chain is intact, the main tables
have rows, a sample of documents in the backed-up store decrypt to what was uploaded, and how old
the backup was (the recovery point)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.audit.verify import verify_chain
from app.files.store import EncryptedStore
from app.models import Client, Cycle, DocumentVersion, Invoice, LedgerEntry, User
from app.timeutil import utcnow

API = Path(__file__).resolve().parents[2]
SAMPLE = 20
RPO_HOURS = 24


@dataclass(frozen=True)
class Line:
    ok: bool
    text: str


def backup_age_hours(name: str, now: datetime | None = None) -> float | None:
    found = re.search(r"(\d{8}-\d{6})", name)
    if not found:
        return None
    taken = datetime.strptime(found.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    return ((now or utcnow()) - taken).total_seconds() / 3600


def check(db: Session, store: EncryptedStore, backup_name: str) -> list[Line]:
    out: list[Line] = []
    head = ScriptDirectory.from_config(Config(str(API / "alembic.ini"))).get_current_head()
    current = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    out.append(Line(current == head, f"schema at {current} (latest {head})"))

    chain = verify_chain(db)
    out.append(
        Line(
            chain.ok,
            f"audit log chain intact, {chain.entries_checked} entries"
            if chain.ok
            else f"audit log chain BROKEN at entry {chain.first_bad_seq}: {chain.problem}",
        )
    )
    counts = {
        name: int(db.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in (
            ("users", User),
            ("clients", Client),
            ("cycles", Cycle),
            ("invoices", Invoice),
            ("ledger entries", LedgerEntry),
            ("document versions", DocumentVersion),
        )
    }
    out.append(Line(counts["users"] > 0, ", ".join(f"{n} {k}" for k, n in counts.items())))

    sample = list(db.scalars(select(DocumentVersion).order_by(func.rand()).limit(SAMPLE)))
    bad = 0
    for version in sample:
        try:
            data = store.get(version.storage_key)
            if hashlib.sha256(data).hexdigest() != version.sha256:
                bad += 1
        except Exception:
            bad += 1
    out.append(
        Line(
            bad == 0,
            f"{len(sample) - bad} of {len(sample)} sampled documents decrypt and match"
            if sample
            else "no documents to sample",
        )
    )
    age = backup_age_hours(backup_name)
    out.append(
        Line(
            age is not None and age <= RPO_HOURS,
            f"backup taken {age:.1f} hours ago (recovery point limit {RPO_HOURS})"
            if age is not None
            else "backup time unknown",
        )
    )
    return out
