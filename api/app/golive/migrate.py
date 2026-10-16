"""Client migration (C32, LL-132; BRD section 15, Q5).

The source system's clients come in as one CSV file (columns below; UTF-8, the header row first).
Three steps, each safe to repeat:

1. Check (the default): every row is checked as the client screen would check it (PAN and GSTIN
   form, contacts, owner, package, duplicates in the file and in Ledgerline). Nothing is saved;
   rows with problems are listed with the reason.
2. `--commit`: refused while any row has a problem. Each row is saved through the API as the named
   Administrator, so it is audited and its cycles are made exactly as for a client typed in. A row
   whose PAN or GSTIN is already in Ledgerline is left alone, so a stopped run can be run again.
   The source id and the new client id of every row are written to the map file (no PAN in it).
3. Verify (after a commit, or alone with `--verify`): the count check (every row of the file is a
   client in Ledgerline, found by PAN or GSTIN) and the sample check (5% of the rows, at least one,
   chosen at random, compared field by field with what Ledgerline holds).

PAN, GSTIN, email and phone never appear in the report; rows are named by line and source id.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import client_import
from app.client_import import COLUMNS, ImportFileError, Row, existing
from app.golive.inproc import Api, ApiError
from app.models import Client, Contact, Package, User

__all__ = ["COLUMNS", "Report", "check", "commit", "read", "verify", "write_map"]

REQUIRED = ("source_id", "owner_email", *client_import.REQUIRED)
MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 20_000
SAMPLE_PERCENT = 5


@dataclass
class Report:
    rows: list[Row]
    saved: list[tuple[str, int]] = field(default_factory=list)
    already: list[tuple[str, int]] = field(default_factory=list)
    failed: list[tuple[Row, str]] = field(default_factory=list)
    checks: list[tuple[bool, str]] = field(default_factory=list)

    @property
    def bad(self) -> list[Row]:
        return [r for r in self.rows if r.problems]

    @property
    def ok(self) -> bool:
        return not self.bad and not self.failed and all(ok for ok, _ in self.checks)

    def text(self) -> str:
        lines = [f"{len(self.rows)} rows read; {len(self.bad)} with problems."]
        lines += [
            f"  line {r.line} ({r.source_id or 'no source id'}): {p}"
            for r in self.bad
            for p in r.problems
        ]
        if self.saved or self.already or self.failed:
            lines.append(
                f"{len(self.saved)} saved, {len(self.already)} already in Ledgerline,"
                f" {len(self.failed)} refused."
            )
            lines += [f"  line {r.line} ({r.source_id}): {why}" for r, why in self.failed]
        lines += [f"{'PASS' if ok else 'FAIL'}  {what}" for ok, what in self.checks]
        return "\n".join(lines)


def read(path: Path) -> list[dict[str, str]]:
    """The file's rows as dicts keyed by the known column names (lower case)."""
    if path.stat().st_size > MAX_BYTES:
        raise SystemExit(f"{path} is larger than {MAX_BYTES // (1024 * 1024)} MB; split it.")
    try:
        return client_import.parse(
            path.read_bytes(), max_bytes=MAX_BYTES, max_rows=MAX_ROWS, required=REQUIRED
        )
    except ImportFileError as exc:
        raise SystemExit(str(exc)) from exc


def check(db: Session, raw_rows: list[dict[str, str]]) -> Report:
    """Every row checked as the client form would (see app/client_import). A row already in
    Ledgerline is fine here: it is left alone on commit, so a stopped run can be repeated."""
    return Report(rows=client_import.check(db, raw_rows, require_source_id=True))


def commit(db: Session, api: Api, report: Report) -> None:
    if report.bad:
        raise SystemExit("Not saved: fix the rows with problems first.\n" + report.text())
    for row in report.rows:
        if row.body is None:  # checked rows always have one; never save a half-read row
            raise RuntimeError(f"line {row.line} was not checked")
        db.rollback()  # a fresh snapshot: see rows committed through the API since
        found = existing(db, row)
        if found:
            report.already.append((row.source_id, found.pop()))
            continue
        try:
            made = api.post("/api/clients", row.body.model_dump(mode="json"))
        except ApiError as exc:
            report.failed.append((row, exc.message))
            continue
        report.saved.append((row.source_id, int(made["client_id"])))


def write_map(report: Report, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.writer(out)
        writer.writerow(["source_id", "client_id", "result"])
        for source_id, client_id in report.saved:
            writer.writerow([source_id, client_id, "saved"])
        for source_id, client_id in report.already:
            writer.writerow([source_id, client_id, "already there"])
    path.chmod(0o600)


def verify(db: Session, report: Report, percent: int = SAMPLE_PERCENT) -> None:
    """The count check and the sample check (BRD section 15)."""
    db.rollback()
    rows = [r for r in report.rows if r.body is not None and not r.problems]
    found = {r.line: existing(db, r) for r in rows}
    present = [r for r in rows if len(found[r.line]) == 1]
    report.checks.append(
        (
            len(present) == len(report.rows),
            f"count: {len(report.rows)} rows in the file, {len(present)} found in Ledgerline",
        )
    )
    if not present:
        return
    size = min(len(present), max(1, math.ceil(len(present) * percent / 100)))
    sample = random.SystemRandom().sample(present, size)
    differ = []
    for row in sample:
        client_id = found[row.line].pop()
        wrong = _compare(db, row, client_id)
        if wrong:
            differ.append(f"line {row.line} ({row.source_id}): {', '.join(wrong)} differ")
    report.checks.append(
        (
            not differ,
            f"sample: {size} of {len(present)} rows ({percent}%) compared field by field"
            + ("" if not differ else "; " + "; ".join(differ)),
        )
    )


def _compare(db: Session, row: Row, client_id: int) -> list[str]:
    body = row.body
    client = db.scalar(select(Client).where(Client.client_id == client_id))
    if body is None or client is None:
        return ["the whole row"]
    contact = db.scalar(
        select(Contact).where(Contact.client_pk == client.id, Contact.is_primary.is_(True))
    )
    owner = db.get(User, client.owner_id)
    package = db.get(Package, client.package_id)
    want: dict[str, Any] = {
        "kind": body.kind,
        "legal_name": " ".join(body.legal_name.split()),
        "pan": body.pan,
        "gstin": body.gstin,
        "owner": row.owner_email,
        "package": row.package.lower(),
        "contact_name": " ".join(body.contacts[0].name.split()),
        "contact_email": str(body.contacts[0].email).lower(),
        "contact_phone": body.contacts[0].phone,
    }
    have: dict[str, Any] = {
        "kind": client.kind,
        "legal_name": client.legal_name,
        "pan": client.pan,
        "gstin": client.gstin,
        "owner": owner.email if owner else None,
        "package": package.name.lower() if package else None,
        "contact_name": contact.name if contact else None,
        "contact_email": contact.email if contact else None,
        "contact_phone": contact.phone if contact else None,
    }
    return [name for name in want if want[name] != have[name]]
