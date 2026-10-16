"""Load the firm's filing types, due-date rules and packages from one JSON file (C32, LL-133, Q4).

    {
      "filings": [
        {"code": "GSTR-3B", "name": "GSTR-3B monthly return", "description": "...",
         "default_checklist": [{"label": "Sales register", "required": true}],
         "calendar": {"frequency": "monthly", "due": {"months_after": 1, "day": 20},
                      "holiday_rule": "earlier_working_day", "notes": "..."}}
      ],
      "packages": [
        {"name": "GST Monthly", "description": "...", "fee": "1500", "billing_frequency": "monthly",
         "filings": ["GSTR-1", "GSTR-3B"]}
      ]
    }

Check (the default) validates every entry with the same rules as the admin screens and previews
each calendar for the current year; nothing is saved. `--commit` creates each filing, saves and
publishes its calendar, then creates and publishes each package, all through the API as the named
Administrator (so it is audited). A filing whose code, or a package whose name, already exists is
left alone, so the file can be loaded again after it is extended.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.golive.inproc import Api, ApiError
from app.models import BillingFrequency
from app.routers.admin_filings import CalendarIn, FilingIn
from app.routers.admin_packages import PackageIn

MAX_BYTES = 1024 * 1024


class FilingEntry(FilingIn):
    calendar: CalendarIn


class PackageEntry(PackageIn):
    fee: Decimal = Field(gt=0, le=Decimal("99999999.99"), decimal_places=2)
    billing_frequency: BillingFrequency
    filings: list[str] = Field(min_length=1, max_length=50)


class CatalogFile(BaseModel):
    filings: list[FilingEntry] = Field(default_factory=list, max_length=200)
    packages: list[PackageEntry] = Field(default_factory=list, max_length=200)


@dataclass
class Result:
    problems: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def text(self) -> str:
        return "\n".join([*self.done, *(f"PROBLEM  {p}" for p in self.problems)])


def read(path: Path) -> tuple[CatalogFile | None, Result]:
    result = Result()
    if path.stat().st_size > MAX_BYTES:
        result.problems.append(f"{path} is larger than 1 MB.")
        return None, result
    try:
        catalog = CatalogFile.model_validate(json.loads(path.read_bytes()))
    except ValueError as exc:  # JSON errors and ValidationError
        if isinstance(exc, ValidationError):
            for error in exc.errors():
                where = ".".join(str(p) for p in error["loc"])
                result.problems.append(f"{where}: {error['msg']}")
        else:
            result.problems.append(f"Not valid JSON: {exc}")
        return None, result
    codes = [f.code for f in catalog.filings]
    for code in {c for c in codes if codes.count(c) > 1}:
        result.problems.append(f"filing {code} is listed twice.")
    names = [p.name.lower() for p in catalog.packages]
    for name in {n for n in names if names.count(n) > 1}:
        result.problems.append(f"package '{name}' is listed twice.")
    return catalog, result


def check(api: Api, catalog: CatalogFile, result: Result) -> None:
    existing = {f["code"] for f in api.get("/api/admin/filings")}
    for entry in catalog.filings:
        try:
            preview = api.post("/api/admin/filings/preview", entry.calendar.model_dump(mode="json"))
        except ApiError as exc:
            result.problems.append(f"filing {entry.code}: {exc.message}")
            continue
        first = preview["rows"][0] if preview["rows"] else None
        note = " (already in Ledgerline; left alone)" if entry.code in existing else ""
        result.done.append(
            f"filing {entry.code}: {entry.calendar.frequency.value},"
            f" {len(preview['rows'])} periods in {preview['year_label']}"
            + (f", first due {first['due_date']}" if first else "")
            + note
        )
    known = existing | {f.code for f in catalog.filings}
    for package in catalog.packages:
        for code in package.filings:
            if code not in known:
                result.problems.append(f"package '{package.name}': filing {code} is not defined.")


def commit(api: Api, catalog: CatalogFile, result: Result) -> None:
    if result.problems:
        raise SystemExit("Not loaded: fix the problems first.\n" + result.text())
    result.done.clear()
    ids = {f["code"]: f["id"] for f in api.get("/api/admin/filings")}
    for entry in catalog.filings:
        if entry.code in ids:
            result.done.append(f"filing {entry.code}: already there")
            continue
        made = api.post(
            "/api/admin/filings",
            {
                "code": entry.code,
                "name": entry.name,
                "description": entry.description,
                "default_checklist": [c.model_dump() for c in entry.default_checklist],
            },
        )
        ids[entry.code] = made["id"]
        cal = f"/api/admin/filings/{made['id']}/calendar"
        api.put(f"{cal}/draft", entry.calendar.model_dump(mode="json"))
        api.post(f"{cal}/publish")
        result.done.append(f"filing {entry.code}: created, calendar published")

    packages = {p["name"].lower() for p in api.get("/api/admin/packages")}
    for package in catalog.packages:
        if package.name.lower() in packages:
            result.done.append(f"package '{package.name}': already there")
            continue
        made = api.post(
            "/api/admin/packages", {"name": package.name, "description": package.description}
        )
        draft: dict[str, Any] = {
            "fee": str(package.fee),
            "billing_frequency": package.billing_frequency.value,
            "filings": [{"filing_id": ids[code]} for code in package.filings],
        }
        api.put(f"/api/admin/packages/{made['id']}/draft", draft)
        api.post(f"/api/admin/packages/{made['id']}/publish")
        result.done.append(f"package '{package.name}': created and published")
