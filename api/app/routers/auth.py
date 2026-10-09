from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import passwords, throttle
from app.auth.deps import ANY_PERSON, Principal, get_db, require_roles
from app.auth.events import security_event
from app.auth.sessions import (
    SESSION_LIFETIME,
    cookie_name,
    create_session,
    revoke_all_for_user,
    revoke_session,
)
from app.config import Settings
from app.models import Role, User, UserSession
from app.timeutil import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_FAILURE = "Email or password is incorrect, or the account is temporarily locked."


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: Role
    must_change_password: bool


class SessionOut(BaseModel):
    user: UserOut
    csrf_token: str


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _session_out(principal: Principal) -> SessionOut:
    return SessionOut(
        user=UserOut(
            id=principal.user_id,
            email=principal.email,
            full_name=principal.full_name,
            role=principal.role,
            must_change_password=principal.must_change_password,
        ),
        csrf_token=principal.csrf_token,
    )


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> SessionOut:
    settings: Settings = request.app.state.settings
    ip = _client_ip(request)
    now = utcnow()

    if throttle.ip_is_throttled(db, ip, now):
        security_event("login.throttled", ip=ip)
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Wait a few minutes and try again.",
            headers={"Retry-After": str(int(throttle.IP_WINDOW.total_seconds()))},
        )

    user = db.scalar(select(User).where(User.email == body.email.lower()))
    can_try = (
        user is not None
        and user.is_active
        and user.role is not Role.CONSULTANT  # consultants sign in by emailed link (chunk C21)
        and user.password_hash is not None
        and not throttle.is_locked(user, now)
    )
    stored_hash = user.password_hash if user is not None else None
    verified = False
    if can_try and stored_hash is not None:
        verified = passwords.verify_password(stored_hash, body.password)
    else:
        passwords.burn_time(body.password)

    if not verified or user is None or stored_hash is None:
        throttle.record_attempt(db, ip, False, now)
        if can_try and user is not None:
            locked_now = throttle.register_failure(user, now)
            security_event("login.failed", user_id=user.id, ip=ip)
            if locked_now:
                security_event("login.locked", user_id=user.id, ip=ip)
        else:
            security_event("login.failed", user_id=user.id if user else None, ip=ip)
        db.commit()
        raise HTTPException(status_code=401, detail=GENERIC_FAILURE)

    throttle.register_success(user)
    throttle.record_attempt(db, ip, True, now)
    if passwords.needs_rehash(stored_hash):
        user.password_hash = passwords.hash_password(body.password)
    user.last_login_at = now
    token, session_row = create_session(db, user, ip)
    db.commit()

    response.set_cookie(
        cookie_name(settings),
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )
    security_event("login.succeeded", user_id=user.id, role=user.role.value, ip=ip)
    return SessionOut(
        user=UserOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            must_change_password=user.must_change_password,
        ),
        csrf_token=session_row.csrf_token,
    )


@router.get("/me")
def me(
    principal: Principal = Depends(require_roles(*ANY_PERSON, allow_password_change_pending=True)),  # noqa: B008
) -> SessionOut:
    return _session_out(principal)


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    principal: Principal = Depends(require_roles(*ANY_PERSON, allow_password_change_pending=True)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    settings: Settings = request.app.state.settings
    row = db.get(UserSession, principal.session_id)
    if row is not None:
        revoke_session(db, row)
        db.commit()
    security_event("logout", user_id=principal.user_id)
    response = Response(status_code=204)
    response.delete_cookie(cookie_name(settings), path="/")
    return response


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordIn,
    principal: Principal = Depends(require_roles(*ANY_PERSON, allow_password_change_pending=True)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    user = db.get(User, principal.user_id)
    if user is None or user.password_hash is None:
        raise HTTPException(status_code=403, detail="This account does not use a password.")
    now = utcnow()
    if throttle.is_locked(user, now) or not passwords.verify_password(
        user.password_hash, body.current_password
    ):
        if not throttle.is_locked(user, now) and throttle.register_failure(user, now):
            security_event("login.locked", user_id=user.id, via="change-password")
        security_event("password.change_failed", user_id=user.id)
        db.commit()
        raise HTTPException(status_code=400, detail="Your current password is not correct.")

    problems = passwords.check_policy(body.new_password, email=user.email, full_name=user.full_name)
    if passwords.verify_password(user.password_hash, body.new_password):
        problems.append("Choose a password you have not used just now.")
    if problems:
        raise HTTPException(status_code=422, detail=" ".join(problems))

    user.password_hash = passwords.hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = now
    throttle.register_success(user)
    revoke_all_for_user(db, user.id, except_session_id=principal.session_id)
    db.commit()
    security_event("password.changed", user_id=user.id)
    return Response(status_code=204)
