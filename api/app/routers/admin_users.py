"""People and access (BRD FR-AC-02): the Administrator creates and disables accounts."""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import passwords, throttle
from app.auth.deps import Principal, get_db, require_roles
from app.auth.events import security_event
from app.auth.sessions import revoke_all_for_user
from app.models import Role, User
from app.timeutil import today_ist, utcnow

router = APIRouter(prefix="/api/admin/users", tags=["admin: people"])

admin_only = require_roles(Role.ADMINISTRATOR)


class CreateUserIn(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=120)
    role: Role
    agreement_signed_on: date | None = None

    @field_validator("full_name")
    @classmethod
    def _tidy(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a name.")
        return value


class PersonOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: Role
    is_active: bool
    must_change_password: bool
    agreement_signed_on: date | None
    last_login_at: datetime | None
    created_at: datetime


class CreatedOut(BaseModel):
    person: PersonOut
    temporary_password: str | None  # shown once; consultants have none (they use emailed links)


class ResetOut(BaseModel):
    temporary_password: str


def _out(user: User) -> PersonOut:
    return PersonOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        agreement_signed_on=user.agreement_signed_on,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _get(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Person not found.")
    return user


@router.get("")
def list_people(
    _: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> list[PersonOut]:
    users = db.scalars(select(User).order_by(User.full_name, User.id)).all()
    return [_out(u) for u in users]


@router.post("", status_code=201)
def create_person(
    body: CreateUserIn,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> CreatedOut:
    if body.role is Role.CONSULTANT:
        if body.agreement_signed_on is None:
            raise HTTPException(
                status_code=422,
                detail="Enter the date the consultant signed the confidentiality agreement.",
            )
        if body.agreement_signed_on > today_ist():
            raise HTTPException(
                status_code=422, detail="The agreement date cannot be in the future."
            )
    elif body.agreement_signed_on is not None:
        raise HTTPException(status_code=422, detail="Only consultants have an agreement date.")

    temp: str | None = None
    password_hash: str | None = None
    if body.role is not Role.CONSULTANT:
        temp = passwords.generate_temporary_password()
        password_hash = passwords.hash_password(temp)

    user = User(
        email=body.email.lower(),
        full_name=body.full_name,
        role=body.role,
        password_hash=password_hash,
        must_change_password=temp is not None,
        agreement_signed_on=body.agreement_signed_on,
        created_by_id=actor.user_id,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Someone with that email already exists."
        ) from None
    security_event("user.created", actor_id=actor.user_id, user_id=user.id, role=user.role.value)
    return CreatedOut(person=_out(user), temporary_password=temp)


@router.post("/{user_id}/disable")
def disable_person(
    user_id: int,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PersonOut:
    user = _get(db, user_id)
    if user.id == actor.user_id:
        raise HTTPException(status_code=409, detail="You cannot disable your own account.")
    if user.role is Role.ADMINISTRATOR and user.is_active:
        # Lock the active administrators so two simultaneous requests cannot remove the last one.
        admins = db.scalars(
            select(User)
            .where(User.role == Role.ADMINISTRATOR, User.is_active.is_(True))
            .with_for_update()
        ).all()
        if len(admins) <= 1:
            raise HTTPException(
                status_code=409, detail="There must be at least one active Administrator."
            )
    if user.is_active:
        user.is_active = False
        user.disabled_at = utcnow()
    ended = revoke_all_for_user(db, user.id)
    db.commit()
    security_event("user.disabled", actor_id=actor.user_id, user_id=user.id, sessions_ended=ended)
    return _out(user)


@router.post("/{user_id}/enable")
def enable_person(
    user_id: int,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> PersonOut:
    user = _get(db, user_id)
    user.is_active = True
    user.disabled_at = None
    throttle.register_success(user)
    db.commit()
    security_event("user.enabled", actor_id=actor.user_id, user_id=user.id)
    return _out(user)


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: int,
    actor: Principal = Depends(admin_only),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> ResetOut:
    user = _get(db, user_id)
    if user.role is Role.CONSULTANT:
        raise HTTPException(status_code=409, detail="Consultants do not use a password.")
    temp = passwords.generate_temporary_password()
    user.password_hash = passwords.hash_password(temp)
    user.must_change_password = True
    user.password_changed_at = utcnow()
    throttle.register_success(user)
    revoke_all_for_user(db, user.id)
    db.commit()
    security_event("user.password_reset", actor_id=actor.user_id, user_id=user.id)
    return ResetOut(temporary_password=temp)
