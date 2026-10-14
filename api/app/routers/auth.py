"""Sign-in in stages (BRD NFR-SE-02): password, then a second step. No Staff or Administrator
session exists until the second step succeeds; a correct password only earns a challenge.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import challenges, mfa, passwords, throttle
from app.auth.deps import ANY_PERSON, Principal, get_db, require_roles
from app.auth.events import security_event
from app.auth.sessions import (
    IDLE_ROLES,
    SESSION_LIFETIME,
    cookie_name,
    create_session,
    revoke_all_for_user,
    revoke_session,
)
from app.config import Settings
from app.errors import ApiHTTPException
from app.models import ChallengePurpose, LoginChallenge, RecoveryCode, Role, User, UserSession
from app.timeutil import utcnow

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_FAILURE = "Email or password is incorrect, or the account is temporarily locked."
BAD_CODE = "That code is not correct."
EXPIRED_CHALLENGE = "That sign-in has expired. Start again."


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class ChallengeIn(BaseModel):
    challenge: str = Field(min_length=20, max_length=200)


class VerifyIn(ChallengeIn):
    code: str = Field(min_length=1, max_length=64)


class PasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(PasswordIn):
    new_password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: Role
    must_change_password: bool
    mfa_enabled: bool


class SessionOut(BaseModel):
    status: Literal["signed_in"] = "signed_in"
    user: UserOut
    csrf_token: str
    recovery_codes_left: int | None = None


class ChallengeOut(BaseModel):
    status: Literal["mfa_required", "mfa_enrolment_required"]
    challenge: str


class EnrolStartOut(BaseModel):
    secret: str  # grouped for typing by hand
    otpauth_uri: str
    qr: str  # data: URI of an SVG


class EnrolDoneOut(SessionOut):
    recovery_codes: list[str]  # shown once


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _key(request: Request) -> str:
    settings: Settings = request.app.state.settings
    return settings.secret_key.get_secret_value()


def needs_second_step(role: Role) -> bool:
    return role in IDLE_ROLES  # Administrator and Staff


def _too_many(ip: str) -> ApiHTTPException:
    security_event("login.throttled", ip=ip)
    return ApiHTTPException(
        429,
        "Too many attempts. Wait a few minutes and try again.",
        code="throttled",
        headers={"Retry-After": str(int(throttle.IP_WINDOW.total_seconds()))},
    )


def _codes_left(db: Session, user: User) -> int | None:
    if user.totp_enabled_at is None:
        return None
    return int(
        db.scalar(
            select(func.count())
            .select_from(RecoveryCode)
            .where(RecoveryCode.user_id == user.id, RecoveryCode.used_at.is_(None))
        )
        or 0
    )


def _session_out(db: Session, user: User, csrf: str) -> SessionOut:
    return SessionOut(
        user=UserOut(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=user.role,
            must_change_password=user.must_change_password,
            mfa_enabled=user.totp_enabled_at is not None,
        ),
        csrf_token=csrf,
        recovery_codes_left=_codes_left(db, user),
    )


def _sign_in(db: Session, user: User, request: Request, response: Response) -> SessionOut:
    """Create the session. Only reached once every required step has succeeded."""
    settings: Settings = request.app.state.settings
    throttle.register_success(user)
    user.last_login_at = utcnow()
    token, row = create_session(db, user, _ip(request))
    security_event("login.succeeded", db=db, user_id=user.id, role=user.role.value, ip=_ip(request))
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
    return _session_out(db, user, row.csrf_token)


def _second_step_failed(
    db: Session, user: User, ch: LoginChallenge, ip: str, now: datetime
) -> ApiHTTPException:
    ch.attempts += 1
    throttle.record_attempt(db, ip, False, now)
    locked = throttle.register_failure(user, now)
    security_event("mfa.failed", db=db, user_id=user.id, ip=ip)
    if locked:
        security_event("login.locked", db=db, user_id=user.id, ip=ip, via="second-step")
    db.commit()
    return ApiHTTPException(401, BAD_CODE, code="bad_code")


def _live_challenge(
    db: Session, token: str, purpose: ChallengePurpose, ip: str
) -> tuple[LoginChallenge, User]:
    now = utcnow()
    if throttle.ip_is_throttled(db, ip, now):
        raise _too_many(ip)
    ch = challenges.load(db, token, purpose, now)
    user = db.get(User, ch.user_id) if ch else None
    if (
        ch is None
        or user is None
        or not user.is_active
        or throttle.is_locked(user, now)
        or not needs_second_step(user.role)
    ):
        security_event("mfa.challenge_rejected", user_id=user.id if user else None, ip=ip)
        raise ApiHTTPException(401, EXPIRED_CHALLENGE, code="challenge_expired")
    return ch, user


def _replace_recovery_codes(db: Session, user: User, key: str) -> list[str]:
    db.execute(delete(RecoveryCode).where(RecoveryCode.user_id == user.id))
    codes = mfa.new_recovery_codes()
    now = utcnow()
    for code in codes:
        db.add(
            RecoveryCode(user_id=user.id, code_hash=mfa.recovery_hash(code, key), created_at=now)
        )
    return codes


def _use_recovery_code(db: Session, user: User, code: str, key: str) -> bool:
    row = db.scalar(
        select(RecoveryCode)
        .where(
            RecoveryCode.user_id == user.id,
            RecoveryCode.code_hash == mfa.recovery_hash(code, key),
            RecoveryCode.used_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        return False
    row.used_at = utcnow()
    return True


@router.post("/login")
def login(
    body: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> SessionOut | ChallengeOut:
    ip = _ip(request)
    now = utcnow()
    if throttle.ip_is_throttled(db, ip, now):
        raise _too_many(ip)

    user = db.scalar(select(User).where(User.email == body.email.lower()))
    stored_hash = user.password_hash if user is not None else None
    can_try = (
        user is not None
        and user.is_active
        and user.role is not Role.CONSULTANT  # consultants sign in by emailed link (chunk C21)
        and stored_hash is not None
        and not throttle.is_locked(user, now)
    )
    verified = False
    if can_try and stored_hash is not None:
        verified = passwords.verify_password(stored_hash, body.password)
    else:
        passwords.burn_time(body.password)

    if not verified or user is None or stored_hash is None:
        throttle.record_attempt(db, ip, False, now)
        if can_try and user is not None:
            locked_now = throttle.register_failure(user, now)
            security_event("login.failed", db=db, user_id=user.id, ip=ip)
            if locked_now:
                security_event("login.locked", db=db, user_id=user.id, ip=ip)
        else:
            security_event("login.failed", db=db, user_id=user.id if user else None, ip=ip)
        db.commit()
        raise ApiHTTPException(401, GENERIC_FAILURE, code="bad_credentials")

    if passwords.needs_rehash(stored_hash):
        user.password_hash = passwords.hash_password(body.password)

    if not needs_second_step(user.role):
        return _sign_in(db, user, request, response)

    # A correct password is not a sign-in. It earns a short-lived challenge and nothing else,
    # and it does not clear the failure count: only a completed sign-in does.
    purpose = ChallengePurpose.VERIFY if user.totp_enabled_at else ChallengePurpose.ENROL
    token = challenges.issue(db, user, purpose, now)
    security_event("login.password_ok", db=db, user_id=user.id, next_step=purpose.value, ip=ip)
    db.commit()
    return ChallengeOut(
        status="mfa_required" if purpose is ChallengePurpose.VERIFY else "mfa_enrolment_required",
        challenge=token,
    )


@router.post("/mfa/verify")
def mfa_verify(
    body: VerifyIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> SessionOut:
    ip = _ip(request)
    ch, user = _live_challenge(db, body.challenge, ChallengePurpose.VERIFY, ip)
    key = _key(request)
    now = utcnow()
    secret = mfa.decrypt_secret(user.totp_secret_enc, key)
    if secret is None:
        raise ApiHTTPException(401, EXPIRED_CHALLENGE, code="challenge_expired")

    ok = False
    if mfa.looks_like_totp(body.code):
        step = mfa.verify_totp(secret, body.code, now, user.totp_last_step)
        if step is not None:
            user.totp_last_step = step  # this code cannot be used again
            ok = True
    elif _use_recovery_code(db, user, body.code, key):
        security_event("mfa.recovery_code_used", db=db, user_id=user.id)
        ok = True

    if not ok:
        raise _second_step_failed(db, user, ch, ip, now)
    ch.used_at = now
    return _sign_in(db, user, request, response)


@router.post("/mfa/enrol/start")
def mfa_enrol_start(
    body: ChallengeIn,
    request: Request,
    db: Session = Depends(get_db),  # noqa: B008
) -> EnrolStartOut:
    ch, user = _live_challenge(db, body.challenge, ChallengePurpose.ENROL, _ip(request))
    key = _key(request)
    secret = mfa.decrypt_secret(ch.pending_secret_enc, key)
    if secret is None:
        secret = mfa.new_secret()
        ch.pending_secret_enc = mfa.encrypt_secret(secret, key)
        db.commit()
    uri = mfa.otpauth_uri(secret, user.email)
    return EnrolStartOut(secret=mfa.group_secret(secret), otpauth_uri=uri, qr=mfa.qr_data_uri(uri))


@router.post("/mfa/enrol/confirm")
def mfa_enrol_confirm(
    body: VerifyIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),  # noqa: B008
) -> EnrolDoneOut:
    ip = _ip(request)
    ch, user = _live_challenge(db, body.challenge, ChallengePurpose.ENROL, ip)
    key = _key(request)
    now = utcnow()
    secret = mfa.decrypt_secret(ch.pending_secret_enc, key)
    if secret is None:
        raise ApiHTTPException(409, "Start the setup again.", code="enrolment_not_started")
    step = mfa.verify_totp(secret, body.code, now, None)
    if step is None:
        raise _second_step_failed(db, user, ch, ip, now)

    user.totp_secret_enc = ch.pending_secret_enc
    user.totp_enabled_at = now
    user.totp_last_step = step
    codes = _replace_recovery_codes(db, user, key)
    ch.used_at = now
    security_event("mfa.enrolled", db=db, user_id=user.id)
    done = _sign_in(db, user, request, response)
    return EnrolDoneOut(**done.model_dump(), recovery_codes=codes)


@router.get("/me")
def me(
    principal: Principal = Depends(require_roles(*ANY_PERSON, allow_password_change_pending=True)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> SessionOut:
    user = db.get(User, principal.user_id)
    if user is None:
        raise ApiHTTPException(401, "Sign in to continue.", code="unauthenticated")
    return _session_out(db, user, principal.csrf_token)


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
    security_event("logout", db=db, user_id=principal.user_id)
    db.commit()
    response = Response(status_code=204)
    response.delete_cookie(cookie_name(settings), path="/")
    return response


def _check_current_password(db: Session, user: User, password: str, event: str) -> None:
    """Re-check the password for sensitive changes. Wrong guesses count towards the lockout."""
    now = utcnow()
    if (
        user.password_hash is None
        or throttle.is_locked(user, now)
        or not passwords.verify_password(user.password_hash, password)
    ):
        counts = user.password_hash is not None and not throttle.is_locked(user, now)
        if counts and throttle.register_failure(user, now):
            security_event("login.locked", db=db, user_id=user.id, via=event)
        security_event(event, db=db, user_id=user.id)
        db.commit()
        raise ApiHTTPException(400, "Your current password is not correct.", code="bad_password")


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordIn,
    principal: Principal = Depends(require_roles(*ANY_PERSON, allow_password_change_pending=True)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> Response:
    user = db.get(User, principal.user_id)
    if user is None or user.password_hash is None:
        raise ApiHTTPException(403, "This account does not use a password.", code="no_password")
    _check_current_password(db, user, body.current_password, "password.change_failed")

    problems = passwords.check_policy(body.new_password, email=user.email, full_name=user.full_name)
    if passwords.verify_password(user.password_hash, body.new_password):
        problems.append("Choose a password you have not used just now.")
    if problems:
        raise ApiHTTPException(422, " ".join(problems), code="weak_password")

    user.password_hash = passwords.hash_password(body.new_password)
    user.must_change_password = False
    user.password_changed_at = utcnow()
    throttle.register_success(user)
    revoke_all_for_user(db, user.id, except_session_id=principal.session_id)
    security_event("password.changed", db=db, user_id=user.id)
    db.commit()
    return Response(status_code=204)


@router.post("/mfa/recovery-codes")
def new_recovery_codes(
    body: PasswordIn,
    request: Request,
    principal: Principal = Depends(require_roles(Role.ADMINISTRATOR, Role.STAFF)),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
) -> dict[str, list[str]]:
    """Replace the recovery codes (the old ones stop working). Needs the password again."""
    user = db.get(User, principal.user_id)
    if user is None or user.totp_enabled_at is None:
        raise ApiHTTPException(409, "Two-step sign-in is not set up.", code="mfa_not_enabled")
    _check_current_password(db, user, body.current_password, "mfa.recovery_codes_denied")
    codes = _replace_recovery_codes(db, user, _key(request))
    security_event("mfa.recovery_codes_replaced", db=db, user_id=user.id)
    db.commit()
    return {"recovery_codes": codes}
