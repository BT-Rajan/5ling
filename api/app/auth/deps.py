"""Who is calling, and are they allowed. Every route that is not public must use `require_roles`.

A test walks every route and fails if one is neither on the public list nor protected here.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.events import security_event
from app.auth.sessions import cookie_name, load_session, touch
from app.config import Settings
from app.models import Role
from app.timeutil import utcnow

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "x-csrf-token"


@dataclass(frozen=True)
class Principal:
    user_id: int
    email: str
    full_name: str
    role: Role
    session_id: int
    csrf_token: str
    must_change_password: bool


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def _route_label(request: Request) -> str:
    route = request.scope.get("route")
    return f"{request.method} {getattr(route, 'path', 'unmatched')}"


def authenticate(
    request: Request, db: Session, *, allow_password_change_pending: bool = False
) -> Principal:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(cookie_name(settings))
    loaded = load_session(db, token) if token else None
    if loaded is None:
        raise HTTPException(status_code=401, detail="Sign in to continue.")
    session_row, user = loaded

    if request.method in UNSAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not hmac.compare_digest(sent.encode(), session_row.csrf_token.encode()):
            security_event("csrf.rejected", user_id=user.id, route=_route_label(request))
            raise HTTPException(status_code=403, detail="This request could not be verified.")

    if user.must_change_password and not allow_password_change_pending:
        raise HTTPException(status_code=403, detail="Change your password to continue.")

    touch(db, session_row, utcnow())
    db.commit()
    return Principal(
        user_id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        session_id=session_row.id,
        csrf_token=session_row.csrf_token,
        must_change_password=user.must_change_password,
    )


def require_roles(
    *roles: Role, allow_password_change_pending: bool = False
) -> Callable[..., Principal]:
    allowed = frozenset(roles)

    def dependency(request: Request, db: Session = Depends(get_db)) -> Principal:  # noqa: B008
        principal = authenticate(
            request, db, allow_password_change_pending=allow_password_change_pending
        )
        if principal.role not in allowed:
            security_event(
                "access.denied",
                user_id=principal.user_id,
                role=principal.role.value,
                route=_route_label(request),
            )
            raise HTTPException(status_code=403, detail="You do not have access to this.")
        return principal

    dependency.__authz__ = allowed  # type: ignore[attr-defined]  # marker read by the route-walk test
    return dependency


ANY_PERSON = (Role.ADMINISTRATOR, Role.STAFF, Role.CONSULTANT)
