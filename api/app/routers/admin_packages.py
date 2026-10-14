"""Packages (BRD FR-PK-01, FR-PK-05). Administrator only.

A package's contents (filings, checklists, fee, billing frequency) are versioned. The
Administrator edits a draft and publishes it. A package cannot be published without at least one
filing and a fee, and each filing must have a published calendar so cycles can be scheduled.
Editing after publishing starts a new draft; published versions never change.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.deps import Principal, get_db, require_roles
from app.catalog import (
    MAX_CHECKLIST,
    ChecklistEntry,
    next_version,
    paise,
    rupees,
    status_of,
    unique_labels,
)
from app.errors import ApiHTTPException
from app.models import (
    BillingFrequency,
    CalendarVersion,
    ChecklistItem,
    Filing,
    Package,
    PackageFiling,
    PackageVersion,
    Role,
)
from app.timeutil import utcnow

router = APIRouter(prefix="/api/admin/packages", tags=["admin: packages"])

admin_only = require_roles(Role.ADMINISTRATOR)

MAX_FILINGS = 50
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class PackageIn(BaseModel):
    name: Name
    description: Text | None = None


class PackagePatch(BaseModel):
    name: Name | None = None
    description: Text | None = None
    is_active: bool | None = None


class DraftFilingIn(BaseModel):
    filing_id: int = Field(ge=1)
    # Leave out to start from the filing type's checklist template.
    checklist: list[ChecklistEntry] | None = Field(default=None, max_length=MAX_CHECKLIST)

    @field_validator("checklist")
    @classmethod
    def _unique(cls, items: list[ChecklistEntry] | None) -> list[ChecklistEntry] | None:
        return None if items is None else unique_labels(items)


class DraftIn(BaseModel):
    fee: Decimal | None = Field(default=None, gt=0, le=Decimal("99999999.99"), decimal_places=2)
    billing_frequency: BillingFrequency | None = None
    notes: Text | None = None
    filings: list[DraftFilingIn] = Field(default_factory=list, max_length=MAX_FILINGS)

    @field_validator("filings")
    @classmethod
    def _once_each(cls, filings: list[DraftFilingIn]) -> list[DraftFilingIn]:
        ids = [f.filing_id for f in filings]
        if len(ids) != len(set(ids)):
            raise ValueError("A filing type is listed twice.")
        return filings


class ChecklistItemOut(BaseModel):
    id: int
    label: str
    required: bool


class VersionFilingOut(BaseModel):
    filing_id: int
    code: str
    name: str
    checklist: list[ChecklistItemOut]


class VersionOut(BaseModel):
    id: int
    version: int
    status: str
    fee: str | None  # rupees, e.g. "15000.00"
    billing_frequency: BillingFrequency | None
    notes: str | None
    filings: list[VersionFilingOut]
    created_at: datetime
    created_by_id: int
    published_at: datetime | None
    published_by_id: int | None


class PackageSummaryOut(BaseModel):
    id: int
    name: str
    description: str | None
    is_active: bool
    current_version: int | None  # newest published: what clients are given from now on
    current_fee: str | None
    current_billing_frequency: BillingFrequency | None
    current_filings: int
    has_draft: bool


class PackageOut(BaseModel):
    id: int
    name: str
    description: str | None
    is_active: bool
    versions: list[VersionOut]  # newest first


def _versions_out(db: Session, versions: list[PackageVersion]) -> list[VersionOut]:
    ids = [v.id for v in versions]
    filings_by_version: dict[int, list[VersionFilingOut]] = {i: [] for i in ids}
    if ids:
        rows = db.execute(
            select(PackageFiling, Filing.code, Filing.name)
            .join(Filing, Filing.id == PackageFiling.filing_id)
            .where(PackageFiling.package_version_id.in_(ids))
            .order_by(PackageFiling.package_version_id, PackageFiling.position)
        ).all()
        items: dict[int, list[ChecklistItemOut]] = {pf.id: [] for pf, _, _ in rows}
        if items:
            for item in db.scalars(
                select(ChecklistItem)
                .where(ChecklistItem.package_filing_id.in_(list(items)))
                .order_by(ChecklistItem.package_filing_id, ChecklistItem.position)
            ):
                items[item.package_filing_id].append(
                    ChecklistItemOut(id=item.id, label=item.label, required=item.required)
                )
        for pf, code, name in rows:
            filings_by_version[pf.package_version_id].append(
                VersionFilingOut(
                    filing_id=pf.filing_id, code=code, name=name, checklist=items[pf.id]
                )
            )
    return [
        VersionOut(
            id=v.id,
            version=v.version,
            status=status_of(v.published_at),
            fee=rupees(v.fee_paise),
            billing_frequency=v.billing_frequency,
            notes=v.notes,
            filings=filings_by_version[v.id],
            created_at=v.created_at,
            created_by_id=v.created_by_id,
            published_at=v.published_at,
            published_by_id=v.published_by_id,
        )
        for v in versions
    ]


def _package_out(db: Session, package: Package) -> PackageOut:
    versions = list(
        db.scalars(
            select(PackageVersion)
            .where(PackageVersion.package_id == package.id)
            .order_by(PackageVersion.version.desc())
        )
    )
    return PackageOut(
        id=package.id,
        name=package.name,
        description=package.description,
        is_active=package.is_active,
        versions=_versions_out(db, versions),
    )


def _locked(db: Session, package_id: int) -> Package:
    package = db.scalar(select(Package).where(Package.id == package_id).with_for_update())
    if package is None:
        raise ApiHTTPException(404, "No such package.", code="not_found")
    return package


def _draft(db: Session, package_id: int) -> PackageVersion | None:
    return db.scalar(
        select(PackageVersion).where(
            PackageVersion.package_id == package_id, PackageVersion.published_at.is_(None)
        )
    )


def _new_draft(db: Session, package_id: int, actor: Principal) -> PackageVersion:
    draft = PackageVersion(
        package_id=package_id,
        version=next_version(db, PackageVersion, PackageVersion.package_id, package_id),
        created_by_id=actor.user_id,
        created_at=utcnow(),
    )
    db.add(draft)
    db.flush()
    return draft


def _clear(db: Session, draft: PackageVersion) -> None:
    filing_ids = select(PackageFiling.id).where(PackageFiling.package_version_id == draft.id)
    db.execute(delete(ChecklistItem).where(ChecklistItem.package_filing_id.in_(filing_ids)))
    db.execute(delete(PackageFiling).where(PackageFiling.package_version_id == draft.id))


def _audit(db: Session, actor: Principal, event: str, package_id: int, **details: object) -> None:
    audit(
        db,
        event,
        actor_user_id=actor.user_id,
        actor_role=actor.role.value,
        target=("package", package_id),
        details=details,
    )


@router.get("")
def list_packages(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PackageSummaryOut]:
    packages = list(db.scalars(select(Package).order_by(Package.name)))
    versions: dict[int, list[PackageVersion]] = {p.id: [] for p in packages}
    for v in db.scalars(select(PackageVersion).order_by(PackageVersion.version.desc())):
        versions[v.package_id].append(v)
    counts = dict(
        db.execute(
            select(PackageFiling.package_version_id, func.count()).group_by(
                PackageFiling.package_version_id
            )
        ).all()
    )
    out = []
    for p in packages:
        current = next((v for v in versions[p.id] if v.published_at is not None), None)
        out.append(
            PackageSummaryOut(
                id=p.id,
                name=p.name,
                description=p.description,
                is_active=p.is_active,
                current_version=current.version if current else None,
                current_fee=rupees(current.fee_paise) if current else None,
                current_billing_frequency=current.billing_frequency if current else None,
                current_filings=int(counts.get(current.id, 0)) if current else 0,
                has_draft=any(v.published_at is None for v in versions[p.id]),
            )
        )
    return out


@router.post("", status_code=201)
def create_package(
    body: PackageIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PackageOut:
    """A new package starts with an empty draft (version 1)."""
    package = Package(
        name=" ".join(body.name.split()),
        description=body.description or None,
        created_by_id=actor.user_id,
    )
    db.add(package)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiHTTPException(
            409, "A package with that name already exists.", code="duplicate"
        ) from None
    _new_draft(db, package.id, actor)
    _audit(db, actor, "package.created", package.id, name=package.name)
    db.commit()
    return _package_out(db, package)


@router.get("/{package_id}")
def get_package(
    package_id: int,
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PackageOut:
    package = db.get(Package, package_id)
    if package is None:
        raise ApiHTTPException(404, "No such package.", code="not_found")
    return _package_out(db, package)


@router.patch("/{package_id}")
def update_package(
    package_id: int,
    body: PackagePatch,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PackageOut:
    """Name, description, and whether it can be given to new clients. Not versioned: these do
    not change what any client receives."""
    package = _locked(db, package_id)
    changes = body.model_dump(exclude_unset=True)
    if "name" in changes and body.name is None:
        raise ApiHTTPException(422, "A package needs a name.", code="validation_error")
    if body.name is not None:
        package.name = " ".join(body.name.split())
    if "description" in changes:
        package.description = body.description or None
    if body.is_active is not None:
        package.is_active = body.is_active
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiHTTPException(
            409, "A package with that name already exists.", code="duplicate"
        ) from None
    _audit(db, actor, "package.updated", package.id, changed=sorted(changes))
    db.commit()
    return _package_out(db, package)


@router.put("/{package_id}/draft")
def save_draft(
    package_id: int,
    body: DraftIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> VersionOut:
    """Replace the draft's contents. If the newest version is published, this starts a new
    version; the published one is never changed (FR-PK-05)."""
    _locked(db, package_id)
    wanted = [f.filing_id for f in body.filings]
    filings = {f.id: f for f in db.scalars(select(Filing).where(Filing.id.in_(wanted)))}
    missing = [i for i in wanted if i not in filings]
    if missing:
        raise ApiHTTPException(
            422, "Some of those filing types do not exist.", code="unknown_filing"
        )

    draft = _draft(db, package_id) or _new_draft(db, package_id, actor)
    already = set(
        db.scalars(
            select(PackageFiling.filing_id).where(PackageFiling.package_version_id == draft.id)
        )
    )
    retired = [filings[i].code for i in wanted if not filings[i].is_active and i not in already]
    if retired:
        raise ApiHTTPException(
            422,
            f"{', '.join(retired)} is retired and cannot be added.",
            code="retired_filing",
        )

    _clear(db, draft)
    draft.fee_paise = paise(body.fee) if body.fee is not None else None
    draft.billing_frequency = body.billing_frequency
    draft.notes = body.notes or None
    for position, entry in enumerate(body.filings):
        pf = PackageFiling(
            package_version_id=draft.id, filing_id=entry.filing_id, position=position
        )
        db.add(pf)
        db.flush()
        checklist = (
            entry.checklist
            if entry.checklist is not None
            else [ChecklistEntry(**i) for i in filings[entry.filing_id].default_checklist]
        )
        for n, item in enumerate(checklist):
            db.add(
                ChecklistItem(
                    package_filing_id=pf.id, position=n, label=item.label, required=item.required
                )
            )
    db.flush()
    _audit(db, actor, "package.draft_saved", package_id, version=draft.version)
    db.commit()
    return _versions_out(db, [draft])[0]


@router.delete("/{package_id}/draft", status_code=204)
def discard_draft(
    package_id: int,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    _locked(db, package_id)
    draft = _draft(db, package_id)
    if draft is None:
        raise ApiHTTPException(404, "There is no draft to discard.", code="no_draft")
    version = draft.version
    _clear(db, draft)
    db.delete(draft)
    _audit(db, actor, "package.draft_discarded", package_id, version=version)
    db.commit()
    return Response(status_code=204)


def publish_problems(db: Session, package: Package, draft: PackageVersion) -> list[str]:
    """Why this draft cannot be published yet (empty when it can)."""
    problems: list[str] = []
    if not package.is_active:
        problems.append("The package is retired; make it active first.")
    if draft.fee_paise is None:
        problems.append("Set a fee.")
    if draft.billing_frequency is None:
        problems.append("Choose how often the fee is billed.")
    rows = db.execute(
        select(Filing)
        .join(PackageFiling, PackageFiling.filing_id == Filing.id)
        .where(PackageFiling.package_version_id == draft.id)
        .order_by(PackageFiling.position)
    ).scalars()
    filings = list(rows)
    if not filings:
        problems.append("Add at least one filing.")
    scheduled = set(
        db.scalars(
            select(CalendarVersion.filing_id)
            .where(
                CalendarVersion.filing_id.in_([f.id for f in filings]),
                CalendarVersion.published_at.is_not(None),
            )
            .distinct()
        )
    )
    for f in filings:
        if not f.is_active:
            problems.append(f"{f.code} is retired; remove it.")
        elif f.id not in scheduled:
            problems.append(f"{f.code} has no published calendar yet.")
    return problems


@router.post("/{package_id}/publish")
def publish(
    package_id: int,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> VersionOut:
    package = _locked(db, package_id)
    draft = _draft(db, package_id)
    if draft is None:
        raise ApiHTTPException(409, "There is no draft to publish.", code="no_draft")
    problems = publish_problems(db, package, draft)
    if problems:
        raise ApiHTTPException(422, " ".join(problems), code="cannot_publish")
    draft.published_at = utcnow()
    draft.published_by_id = actor.user_id
    _audit(
        db,
        actor,
        "package.published",
        package_id,
        version=draft.version,
        fee=rupees(draft.fee_paise),
        billing_frequency=draft.billing_frequency,
    )
    db.commit()
    return _versions_out(db, [draft])[0]
