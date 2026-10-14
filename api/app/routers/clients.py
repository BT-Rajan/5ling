"""Clients: create, view, channel consent (BRD FR-CL-01 to FR-CL-03, FR-AL-08, CR-01).

Administrators and Staff create clients; Staff only clients they own themselves. Every client
gets a 6-digit client ID on save. A second client with the same PAN or GSTIN is refused, and the
refusal names the existing client so nobody re-keys it.

PAN, GSTIN, email and phone are personal data: they never go into logs or audit details.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import audit
from app.auth.access import RecordAccess, authorize_record
from app.auth.deps import Principal, get_db, require_roles
from app.catalog import rupees
from app.errors import ApiHTTPException
from app.identifiers import PAN_HOLDERS, gstin_problem, pan_in_gstin, pan_problem, tidy
from app.models import (
    FIRST_CLIENT_ID,
    LAST_CLIENT_ID,
    BillingFrequency,
    Channel,
    Client,
    ClientIdCounter,
    ClientKind,
    ClientStatus,
    ConsentChange,
    Contact,
    Package,
    PackageVersion,
    Role,
    User,
)
from app.timeutil import utcnow

router = APIRouter(prefix="/api/clients", tags=["clients"])

staff_or_admin = require_roles(Role.ADMINISTRATOR, Role.STAFF)

MAX_CONTACTS = 10
ClientIdPath = Annotated[int, Path(ge=FIRST_CLIENT_ID, le=LAST_CLIENT_ID)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
LegalName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]

_PHONE_CHARS = re.compile(r"[\s().-]")
_E164 = re.compile(r"^\+[1-9][0-9]{7,14}$")


def normalise_phone(value: str) -> str:
    """Indian mobiles may be typed without +91; anything else must be in +country form."""
    raw = _PHONE_CHARS.sub("", value)
    if re.fullmatch(r"[6-9][0-9]{9}", raw):
        raw = "+91" + raw
    elif re.fullmatch(r"0[6-9][0-9]{9}", raw):
        raw = "+91" + raw[1:]
    elif re.fullmatch(r"91[6-9][0-9]{9}", raw):
        raw = "+" + raw
    if not _E164.match(raw):
        raise ValueError("Enter a mobile number like 98765 43210, or +44 7700 900123 for abroad.")
    return raw


class ContactIn(BaseModel):
    name: Name
    email: EmailStr
    phone: str | None = Field(default=None, max_length=24)
    is_primary: bool = False
    # Consent given when the client was set up. Email is always on (FR-AL-08).
    whatsapp: bool = False
    sms: bool = False

    @field_validator("phone")
    @classmethod
    def _phone(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalise_phone(value)

    @model_validator(mode="after")
    def _consent_needs_phone(self) -> ContactIn:
        if (self.whatsapp or self.sms) and self.phone is None:
            raise ValueError("WhatsApp or SMS needs a mobile number.")
        return self


class ClientIn(BaseModel):
    kind: ClientKind
    legal_name: LegalName
    pan: str | None = Field(default=None, max_length=20)
    gstin: str | None = Field(default=None, max_length=24)
    owner_id: int | None = Field(default=None, ge=1)  # Staff: leave out (they own it)
    package_id: int = Field(ge=1)
    contacts: list[ContactIn] = Field(min_length=1, max_length=MAX_CONTACTS)

    @field_validator("pan")
    @classmethod
    def _pan(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = tidy(value)
        problem = pan_problem(value)
        if problem:
            raise ValueError(problem)
        return value

    @field_validator("gstin")
    @classmethod
    def _gstin(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = tidy(value)
        problem = gstin_problem(value)
        if problem:
            raise ValueError(problem)
        return value

    @field_validator("contacts")
    @classmethod
    def _one_primary(cls, contacts: list[ContactIn]) -> list[ContactIn]:
        if sum(c.is_primary for c in contacts) != 1:
            raise ValueError("Mark exactly one contact as the primary contact.")
        return contacts

    @model_validator(mode="after")
    def _consistent(self) -> ClientIn:
        if self.gstin:
            embedded = pan_in_gstin(self.gstin)
            if self.pan and self.pan != embedded:
                raise ValueError("The PAN inside the GSTIN is different from the PAN entered.")
            if self.pan is None and pan_problem(embedded) is None:
                self.pan = embedded  # so a later client with this PAN is caught as a duplicate
        if self.kind is ClientKind.INDIVIDUAL and self.pan and self.pan[3] != "P":
            holder = PAN_HOLDERS.get(self.pan[3], "not an individual")
            raise ValueError(f"That PAN belongs to a {holder}, not an individual.")
        return self


class ConsentIn(BaseModel):
    channel: Channel
    granted: bool
    note: Note | None = None


class ConsentOut(BaseModel):
    email: bool
    whatsapp: bool
    sms: bool


class ContactOut(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None
    is_primary: bool
    consent: ConsentOut


class PersonRef(BaseModel):
    id: int
    full_name: str


class PackageRef(BaseModel):
    id: int
    name: str
    version: int


class ClientOut(BaseModel):
    client_id: int
    kind: ClientKind
    legal_name: str
    pan: str | None
    gstin: str | None
    status: ClientStatus
    owner: PersonRef
    package: PackageRef
    contacts: list[ContactOut]
    created_at: datetime


class PackageOption(BaseModel):
    id: int
    name: str
    version: int
    fee: str | None
    billing_frequency: BillingFrequency | None


class OptionsOut(BaseModel):
    owners: list[PersonRef]
    packages: list[PackageOption]


def _consents(db: Session, contact_ids: list[int]) -> dict[int, ConsentOut]:
    found: dict[int, dict[Channel, bool]] = {
        i: {Channel.EMAIL: True, Channel.WHATSAPP: False, Channel.SMS: False} for i in contact_ids
    }
    if contact_ids:
        rows = db.scalars(
            select(ConsentChange)
            .where(ConsentChange.contact_id.in_(contact_ids))
            .order_by(ConsentChange.id)
        )
        for row in rows:  # oldest first, so the newest wins
            found[row.contact_id][row.channel] = row.granted
    return {
        i: ConsentOut(email=c[Channel.EMAIL], whatsapp=c[Channel.WHATSAPP], sms=c[Channel.SMS])
        for i, c in found.items()
    }


def _client_out(db: Session, client: Client) -> ClientOut:
    owner = db.get(User, client.owner_id)
    package = db.get(Package, client.package_id)
    version = db.get(PackageVersion, client.package_version_id)
    if owner is None or package is None or version is None:  # foreign keys prevent this
        raise RuntimeError("client refers to a missing owner or package")
    contacts = list(
        db.scalars(select(Contact).where(Contact.client_pk == client.id).order_by(Contact.position))
    )
    consents = _consents(db, [c.id for c in contacts])
    return ClientOut(
        client_id=client.client_id,
        kind=client.kind,
        legal_name=client.legal_name,
        pan=client.pan,
        gstin=client.gstin,
        status=client.status,
        owner=PersonRef(id=owner.id, full_name=owner.full_name),
        package=PackageRef(id=package.id, name=package.name, version=version.version),
        contacts=[
            ContactOut(
                id=c.id,
                name=c.name,
                email=c.email,
                phone=c.phone,
                is_primary=c.is_primary,
                consent=consents[c.id],
            )
            for c in contacts
        ],
        created_at=client.created_at,
    )


def can_open(principal: Principal, client: Client) -> bool:
    return principal.role is Role.ADMINISTRATOR or client.owner_id == principal.user_id


def load_client(db: Session, principal: Principal, client_id: int, *, lock: bool = False) -> Client:
    """The client, if this person may open it (record-level check, NFR-SE-01)."""
    stmt = select(Client).where(Client.client_id == client_id)
    client = db.scalar(stmt.with_for_update() if lock else stmt)
    if client is None:
        authorize_record(principal, None, what="client")  # raises 404
        raise ApiHTTPException(404, "Not found.", code="not_found")
    authorize_record(principal, RecordAccess(owner_id=client.owner_id), what="client")
    return client


def _duplicate(db: Session, principal: Principal, pan: str | None, gstin: str | None) -> None:
    """Refuse a second client with this PAN or GSTIN, naming the one that exists (FR-CL-02)."""
    checks = [
        c
        for c in (Client.pan == pan if pan else None, Client.gstin == gstin if gstin else None)
        if c is not None
    ]
    if not checks:
        return
    existing = db.scalar(select(Client).where(or_(*checks)).limit(1))
    if existing is None:
        return
    matched = "PAN" if pan and existing.pan == pan else "GSTIN"
    visible = can_open(principal, existing)
    who = f" ({existing.legal_name})" if visible else ""
    raise ApiHTTPException(
        409,
        f"Client {existing.client_id}{who} already has this {matched}."
        + ("" if visible else " It belongs to someone else; ask the Administrator."),
        code="duplicate_client",
        extra={
            "existing": {
                "client_id": existing.client_id,
                "matched": matched.lower(),
                "can_open": visible,
                "legal_name": existing.legal_name if visible else None,
            }
        },
    )


def _owner(db: Session, principal: Principal, owner_id: int | None) -> User:
    if principal.role is Role.STAFF:
        if owner_id not in (None, principal.user_id):
            raise ApiHTTPException(
                403, "Staff create clients they own themselves.", code="forbidden_owner"
            )
        owner_id = principal.user_id
    if owner_id is None:
        raise ApiHTTPException(422, "Choose who owns this client.", code="owner_required")
    owner = db.get(User, owner_id)
    if owner is None or not owner.is_active or owner.role is Role.CONSULTANT:
        raise ApiHTTPException(
            422, "The owner must be an active Staff member or Administrator.", code="bad_owner"
        )
    return owner


def current_package_version(db: Session, package_id: int) -> tuple[Package, PackageVersion]:
    package = db.get(Package, package_id)
    version = (
        db.scalar(
            select(PackageVersion)
            .where(
                PackageVersion.package_id == package_id, PackageVersion.published_at.is_not(None)
            )
            .order_by(PackageVersion.version.desc())
            .limit(1)
        )
        if package
        else None
    )
    if package is None or version is None or not package.is_active:
        raise ApiHTTPException(
            422, "Choose an active package that has been published.", code="bad_package"
        )
    return package, version


def _issue_client_id(db: Session) -> int:
    counter = ClientIdCounter(issued_at=utcnow())
    db.add(counter)
    db.flush()
    if counter.client_id > LAST_CLIENT_ID:
        raise ApiHTTPException(503, "No 6-digit client IDs are left.", code="client_ids_exhausted")
    return counter.client_id


@router.get("/options")
def form_options(
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> OptionsOut:
    """What the create-client form offers: possible owners and the packages clients can get."""
    if principal.role is Role.ADMINISTRATOR:
        people = db.scalars(
            select(User)
            .where(User.is_active.is_(True), User.role.in_([Role.STAFF, Role.ADMINISTRATOR]))
            .order_by(User.full_name)
        )
        owners = [PersonRef(id=u.id, full_name=u.full_name) for u in people]
    else:
        owners = [PersonRef(id=principal.user_id, full_name=principal.full_name)]
    latest = (
        select(PackageVersion.package_id, func.max(PackageVersion.version).label("version"))
        .where(PackageVersion.published_at.is_not(None))
        .group_by(PackageVersion.package_id)
        .subquery()
    )
    rows = db.execute(
        select(Package, PackageVersion)
        .join(latest, latest.c.package_id == Package.id)
        .join(
            PackageVersion,
            (PackageVersion.package_id == Package.id)
            & (PackageVersion.version == latest.c.version),
        )
        .where(Package.is_active.is_(True))
        .order_by(Package.name)
    ).all()
    return OptionsOut(
        owners=owners,
        packages=[
            PackageOption(
                id=p.id,
                name=p.name,
                version=v.version,
                fee=rupees(v.fee_paise),
                billing_frequency=v.billing_frequency,
            )
            for p, v in rows
        ],
    )


@router.post("", status_code=201)
def create_client(
    body: ClientIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ClientOut:
    owner = _owner(db, principal, body.owner_id)
    package, version = current_package_version(db, body.package_id)
    _duplicate(db, principal, body.pan, body.gstin)

    client = Client(
        client_id=_issue_client_id(db),
        kind=body.kind,
        legal_name=" ".join(body.legal_name.split()),
        pan=body.pan,
        gstin=body.gstin,
        owner_id=owner.id,
        package_id=package.id,
        package_version_id=version.id,
        status=ClientStatus.ACTIVE,
        created_by_id=principal.user_id,
    )
    db.add(client)
    try:
        db.flush()
    except IntegrityError:
        # Someone saved the same PAN or GSTIN a moment ago.
        db.rollback()
        _duplicate(db, principal, body.pan, body.gstin)
        raise
    now = utcnow()
    for position, entry in enumerate(body.contacts):
        contact = Contact(
            client_pk=client.id,
            name=" ".join(entry.name.split()),
            email=str(entry.email).lower(),
            phone=entry.phone,
            is_primary=entry.is_primary,
            position=position,
            created_at=now,
        )
        db.add(contact)
        db.flush()
        for channel, granted in (
            (Channel.EMAIL, True),
            (Channel.WHATSAPP, entry.whatsapp),
            (Channel.SMS, entry.sms),
        ):
            db.add(
                ConsentChange(
                    contact_id=contact.id,
                    channel=channel,
                    granted=granted,
                    note="Recorded when the client was set up",
                    changed_by_id=principal.user_id,
                    changed_at=now,
                )
            )
    audit(
        db,
        "client.created",
        actor_user_id=principal.user_id,
        actor_role=principal.role.value,
        target=("client", client.client_id),
        details={
            "kind": client.kind,
            "owner_id": owner.id,
            "package_id": package.id,
            "package_version": version.version,
            "contacts": len(body.contacts),
        },
    )
    db.commit()
    return _client_out(db, client)


@router.get("/{client_id}")
def get_client(
    client_id: ClientIdPath,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ClientOut:
    return _client_out(db, load_client(db, principal, client_id))


@router.put("/{client_id}/contacts/{contact_id}/consent")
def change_consent(
    client_id: ClientIdPath,
    contact_id: int,
    body: ConsentIn,
    principal: Principal = Depends(staff_or_admin),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ContactOut:
    """Record a contact opting in to or out of WhatsApp or SMS (FR-AL-08)."""
    client = load_client(db, principal, client_id, lock=True)
    contact = db.scalar(
        select(Contact).where(Contact.id == contact_id, Contact.client_pk == client.id)
    )
    if contact is None:
        raise ApiHTTPException(404, "No such contact for this client.", code="not_found")
    if body.channel is Channel.EMAIL and not body.granted:
        raise ApiHTTPException(
            422, "Email is the minimum channel and cannot be turned off.", code="email_required"
        )
    if body.granted and body.channel is not Channel.EMAIL and contact.phone is None:
        raise ApiHTTPException(422, "Add a mobile number first.", code="phone_required")
    current = _consents(db, [contact.id])[contact.id]
    if getattr(current, body.channel.value) != body.granted:
        db.add(
            ConsentChange(
                contact_id=contact.id,
                channel=body.channel,
                granted=body.granted,
                note=body.note or None,
                changed_by_id=principal.user_id,
                changed_at=utcnow(),
            )
        )
        audit(
            db,
            "client.consent_changed",
            actor_user_id=principal.user_id,
            actor_role=principal.role.value,
            target=("client", client.client_id),
            details={"contact_id": contact.id, "channel": body.channel, "granted": body.granted},
        )
        db.commit()
    consent = _consents(db, [contact.id])[contact.id]
    return ContactOut(
        id=contact.id,
        name=contact.name,
        email=contact.email,
        phone=contact.phone,
        is_primary=contact.is_primary,
        consent=consent,
    )
