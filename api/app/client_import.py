"""Checking a spreadsheet of clients, row by row, before anything is saved (FR-CL-05).

Used by the import screen (C33, `routers/client_import.py`) and by the go-live migration (C32,
`golive/migrate.py`). Each row is checked as the client form would check it: PAN and GSTIN form,
contacts, owner, package, duplicates within the file and against Ledgerline.

The file is CSV in UTF-8 (a spreadsheet saved as "CSV UTF-8"), with a header row naming the
columns below in any order. Problems name the row by line and source id, never by PAN, GSTIN,
email or phone: validation messages that quote what was typed have it hidden.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.identifiers import tidy
from app.models import Client, Package, PackageVersion, Role, User
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
REQUIRED = ("kind", "legal_name", "package", "contact_name", "contact_email")
_YES = {"y", "yes", "true", "1"}
_NO = {"", "n", "no", "false", "0"}
_FIELD_NAMES = {"name": "contact_name", "email": "contact_email", "phone": "contact_phone"}


class ImportFileError(ValueError):
    """The file as a whole cannot be read; nothing in it is checked."""


@dataclass
class Row:
    line: int
    source_id: str
    body: ClientIn | None = None
    owner_email: str = ""
    package: str = ""
    problems: list[str] = field(default_factory=list)
    existing: set[int] = field(default_factory=set)  # client ids with this PAN or GSTIN


def parse(
    data: bytes, *, max_bytes: int, max_rows: int, required: tuple[str, ...] = REQUIRED
) -> list[dict[str, str]]:
    """The file's rows as dicts keyed by the known column names (lower case)."""
    if len(data) > max_bytes:
        raise ImportFileError(f"The file is larger than {max_bytes // 1024} KB; split it.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFileError(
            "The file is not UTF-8 text. Save the spreadsheet as 'CSV UTF-8'."
        ) from exc
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""))
        header = [(h or "").strip().lower() for h in reader.fieldnames or []]
        missing = [c for c in required if c not in header]
        if missing:
            raise ImportFileError(f"The header row lacks: {', '.join(missing)}.")
        unknown = [h for h in header if h not in COLUMNS]
        if unknown:
            raise ImportFileError(
                f"Unknown columns: {', '.join(unknown)[:200]}. Expected: {', '.join(COLUMNS)}."
            )
        if len(set(header)) != len(header):
            raise ImportFileError("A column is named twice in the header row.")
        reader.fieldnames = header
        rows = []
        for raw in reader:
            if None in raw:
                raise ImportFileError(f"Line {reader.line_num} has more cells than the header.")
            if not any(raw.values()):
                continue  # a blank line
            rows.append({k: (v or "").strip() for k, v in raw.items()})
            if len(rows) > max_rows:
                raise ImportFileError(f"More than {max_rows} rows; split the file.")
    except csv.Error as exc:
        raise ImportFileError(f"The file is not readable as CSV: {exc}.") from exc
    if not rows:
        raise ImportFileError("The file has no rows under the header.")
    return rows


def published_packages(db: Session) -> dict[str, int]:
    """Active packages with a published version, by name (lower case)."""
    published = select(PackageVersion.package_id).where(PackageVersion.published_at.is_not(None))
    return {
        name.lower(): pid
        for pid, name in db.execute(
            select(Package.id, Package.name).where(
                Package.is_active.is_(True), Package.id.in_(published)
            )
        )
    }


def check(
    db: Session,
    raw_rows: list[dict[str, str]],
    *,
    staff_owner: User | None = None,
    require_source_id: bool = False,
) -> list[Row]:
    """Check each row. `staff_owner`: a Staff member importing, who owns every row (FR-AC-03)."""
    if staff_owner is None:
        owners = {
            u.email: u.id
            for u in db.scalars(
                select(User).where(
                    User.is_active.is_(True), User.role.in_((Role.STAFF, Role.ADMINISTRATOR))
                )
            )
        }
    else:
        owners = {staff_owner.email: staff_owner.id}
    packages = published_packages(db)
    rows: list[Row] = []
    for n, raw in enumerate(raw_rows, start=2):  # line 1 is the header
        row = Row(line=n, source_id=raw.get("source_id", "")[:64])
        problems = row.problems
        if require_source_id and not row.source_id:
            problems.append("source_id is empty.")
        row.owner_email = raw.get("owner_email", "").lower()
        if staff_owner is not None and not row.owner_email:
            row.owner_email = staff_owner.email
        owner_id = owners.get(row.owner_email)
        if owner_id is None:
            problems.append(
                "owner_email must be your own email: Staff import clients they own."
                if staff_owner is not None
                else "owner_email is not an active Staff member or Administrator."
            )
        row.package = raw.get("package", "")
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
                    "owner_id": owner_id or 1,  # a placeholder only when a problem is listed
                    "package_id": package_id or 1,
                    "contacts": [contact],
                }
            )
        except ValidationError as exc:
            for error in exc.errors():
                where = ".".join(str(p) for p in error["loc"] if p not in ("contacts", 0))
                where = _FIELD_NAMES.get(where, where)
                message = hide(str(error["msg"]).removeprefix("Value error, "), raw)
                problems.append(f"{where}: {message}" if where else message)
        rows.append(row)
    _duplicates(db, rows)
    return rows


def hide(message: str, raw: dict[str, str]) -> str:
    """Validation messages may quote what was typed; personal identifiers never reach a report."""
    for name in ("pan", "gstin", "contact_email", "contact_phone"):
        value = raw.get(name, "")
        for form in {value, tidy(value)} if value else ():
            message = message.replace(form, "[hidden]")
    return message


def existing(db: Session, row: Row) -> set[int]:
    """Client ids that already have this row's PAN or GSTIN."""
    if row.body is None or (row.body.pan is None and row.body.gstin is None):
        return set()
    conditions = []
    if row.body.pan:
        conditions.append(Client.pan == row.body.pan)
    if row.body.gstin:
        conditions.append(Client.gstin == row.body.gstin)
    return set(db.scalars(select(Client.client_id).where(or_(*conditions))))


def _flag(value: str, name: str, problems: list[str]) -> bool:
    if value.lower() in _YES:
        return True
    if value.lower() not in _NO:
        problems.append(f"{name} must be yes or no.")
    return False


def _known(db: Session, pans: set[str], gstins: set[str]) -> tuple[dict[str, int], dict[str, int]]:
    """Client ids by PAN and by GSTIN, for the identifiers given, in a few queries."""
    by_pan: dict[str, int] = {}
    by_gstin: dict[str, int] = {}
    for column, values, into in ((Client.pan, pans, by_pan), (Client.gstin, gstins, by_gstin)):
        ordered = sorted(values)
        for start in range(0, len(ordered), 1000):
            chunk = ordered[start : start + 1000]
            for value, client_id in db.execute(
                select(column, Client.client_id).where(column.in_(chunk))
            ):
                if value is not None:
                    into[value] = client_id
    return by_pan, by_gstin


def _duplicates(db: Session, rows: list[Row]) -> None:
    checked = [r for r in rows if r.body is not None]
    by_pan, by_gstin = _known(
        db,
        {r.body.pan for r in checked if r.body and r.body.pan},
        {r.body.gstin for r in checked if r.body and r.body.gstin},
    )
    seen: dict[str, int] = {}
    for row in checked:
        body = row.body
        if body is None:
            continue
        for kind, value in (("PAN", body.pan), ("GSTIN", body.gstin)):
            if value is None:
                continue
            if value in seen:
                row.problems.append(f"the same {kind} as line {seen[value]}.")
            else:
                seen[value] = row.line
        row.existing = {
            cid
            for cid in (by_pan.get(body.pan or ""), by_gstin.get(body.gstin or ""))
            if cid is not None
        }
        if len(row.existing) > 1:
            row.problems.append("its PAN and GSTIN belong to two different clients already.")
