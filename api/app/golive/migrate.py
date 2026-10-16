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
import io
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.golive.inproc import Api, ApiError
from app.identifiers import tidy
from app.models import Client, Contact, Package, Role, User
from app.routers.clients import ClientIn

COLUMNS = (
    "source_id",
    "kind",
    "legal_name",
    "pan",
    "gstin",
    "owner_email",
    "package",
    "contact_name",
    "contact_email",
    "contact_phone",
    "whatsapp",
    "sms",
)
REQUIRED = ("source_id", "kind", "legal_name", "owner_email", "package", "contact_name")
MAX_BYTES = 20 * 1024 * 1024
MAX_ROWS = 20_000
SAMPLE_PERCENT = 5
_YES = {"y", "yes", "true", "1"}
_NO = {"", "n", "no", "false", "0"}


@dataclass
class Row:
    line: int
    source_id: str
    body: ClientIn | None = None
    owner_email: str = ""
    package: str = ""
    problems: list[str] = field(default_factory=list)


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
    text = path.read_bytes().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    header = [(h or "").strip().lower() for h in reader.fieldnames or []]
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise SystemExit(f"The header row lacks: {', '.join(missing)}.")
    unknown = [h for h in header if h not in COLUMNS]
    if unknown:
        raise SystemExit(f"Unknown columns: {', '.join(unknown)}. Expected: {', '.join(COLUMNS)}.")
    reader.fieldnames = header
    rows = []
    for raw in reader:
        if None in raw:
            raise SystemExit(f"Line {reader.line_num} has more cells than the header.")
        rows.append({k: (v or "").strip() for k, v in raw.items()})
        if len(rows) > MAX_ROWS:
            raise SystemExit(f"More than {MAX_ROWS} rows; split the file.")
    return rows


def _flag(value: str, name: str, problems: list[str]) -> bool:
    if value.lower() in _YES:
        return True
    if value.lower() not in _NO:
        problems.append(f"{name} must be yes or no.")
    return False


def check(db: Session, api: Api, raw_rows: list[dict[str, str]]) -> Report:
    owners = {
        u.email: u.id
        for u in db.scalars(
            select(User).where(
                User.is_active.is_(True), User.role.in_((Role.STAFF, Role.ADMINISTRATOR))
            )
        )
    }
    packages = {
        p["name"].lower(): p["id"]
        for p in api.get("/api/admin/packages")
        if p["is_active"] and p["current_version"] is not None
    }
    rows: list[Row] = []
    for n, raw in enumerate(raw_rows, start=2):  # line 1 is the header
        row = Row(line=n, source_id=raw.get("source_id", "")[:64])
        problems = row.problems
        if not row.source_id:
            problems.append("source_id is empty.")
        row.owner_email = raw.get("owner_email", "").lower()
        row.package = raw.get("package", "")
        owner_id = owners.get(row.owner_email)
        if owner_id is None:
            problems.append("owner_email is not an active Staff member or Administrator.")
        package_id = packages.get(row.package.lower())
        if package_id is None:
            problems.append(f"package '{row.package[:60]}' is not a published, active package.")
        contact = {
            "name": raw.get("contact_name", ""),
            "email": raw.get("contact_email", ""),
            "phone": raw.get("contact_phone") or None,
            "is_primary": True,
            "whatsapp": _flag(raw.get("whatsapp", ""), "whatsapp", problems),
            "sms": _flag(raw.get("sms", ""), "sms", problems),
        }
        try:
            row.body = ClientIn.model_validate(
                {
                    "kind": raw.get("kind", "").lower(),
                    "legal_name": raw.get("legal_name", ""),
                    "pan": raw.get("pan") or None,
                    "gstin": raw.get("gstin") or None,
                    "owner_id": owner_id or 1,
                    "package_id": package_id or 1,
                    "contacts": [contact],
                }
            )
        except ValidationError as exc:
            for error in exc.errors():
                where = ".".join(str(p) for p in error["loc"] if p not in ("contacts", 0))
                where = {
                    "name": "contact_name",
                    "email": "contact_email",
                    "phone": "contact_phone",
                }.get(where, where)
                message = _hide(str(error["msg"]).removeprefix("Value error, "), raw)
                problems.append(f"{where}: {message}" if where else message)
        rows.append(row)

    _duplicates(db, rows)
    return Report(rows=rows)


def _hide(message: str, raw: dict[str, str]) -> str:
    """Validation messages may quote what was typed; personal identifiers never reach the report."""
    for name in ("pan", "gstin", "contact_email", "contact_phone"):
        value = raw.get(name, "")
        for form in {value, tidy(value)} if value else ():
            message = message.replace(form, "[hidden]")
    return message


def _duplicates(db: Session, rows: list[Row]) -> None:
    seen: dict[str, int] = {}
    for row in rows:
        if row.body is None:
            continue
        for kind, value in (("PAN", row.body.pan), ("GSTIN", row.body.gstin)):
            if value is None:
                continue
            if value in seen:
                row.problems.append(f"the same {kind} as line {seen[value]}.")
            else:
                seen[value] = row.line
    # A row already in Ledgerline is fine (a repeated run) only if both identifiers point at the
    # same client; one that matches two different clients is a conflict.
    for row in rows:
        found = _existing(db, row)
        if len(found) > 1:
            row.problems.append("its PAN and GSTIN belong to two different clients already.")


def _existing(db: Session, row: Row) -> set[int]:
    if row.body is None or (row.body.pan is None and row.body.gstin is None):
        return set()
    conditions = []
    if row.body.pan:
        conditions.append(Client.pan == row.body.pan)
    if row.body.gstin:
        conditions.append(Client.gstin == row.body.gstin)
    return set(db.scalars(select(Client.client_id).where(or_(*conditions))))


def commit(db: Session, api: Api, report: Report) -> None:
    if report.bad:
        raise SystemExit("Not saved: fix the rows with problems first.\n" + report.text())
    for row in report.rows:
        if row.body is None:  # checked rows always have one; never save a half-read row
            raise RuntimeError(f"line {row.line} was not checked")
        db.rollback()  # a fresh snapshot: see rows committed through the API since
        found = _existing(db, row)
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
    found = {r.line: _existing(db, r) for r in rows}
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
