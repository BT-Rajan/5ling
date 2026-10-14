"""Every endpoint against every kind of caller (BRD FR-AC-01, NFR-SE-01).

EXPECTED below is written by hand from the BRD, independently of the code. One test proves the
code's own role markers agree with it; the rest call every endpoint as every kind of caller and
check the result. A change to either side has to be deliberate.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

import pytest
from app.main import create_app
from app.models import Role, User, UserSession
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.brd_matrix import CAPABILITIES, SELF_SERVICE, Capability
from tests.conftest import make_settings
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.routewalk import PUBLIC, protected_routes, unprotected_routes

pytestmark = pytest.mark.requires_db

ADMIN = frozenset({Role.ADMINISTRATOR})
ADMIN_AND_STAFF = frozenset({Role.ADMINISTRATOR, Role.STAFF})
EVERYONE = frozenset(Role)

EXPECTED: dict[tuple[str, str], frozenset[Role]] = {
    ("GET", "/api/admin/users"): ADMIN,
    ("POST", "/api/admin/users"): ADMIN,
    ("POST", "/api/admin/users/{user_id}/disable"): ADMIN,
    ("POST", "/api/admin/users/{user_id}/enable"): ADMIN,
    ("POST", "/api/admin/users/{user_id}/reset-password"): ADMIN,
    ("POST", "/api/admin/users/{user_id}/reset-mfa"): ADMIN,
    ("GET", "/api/admin/audit/entries"): ADMIN,
    ("GET", "/api/admin/audit/events"): ADMIN,
    ("GET", "/api/admin/audit/status"): ADMIN,
    ("GET", "/api/admin/audit/checks"): ADMIN,
    ("POST", "/api/admin/audit/verify"): ADMIN,
    ("POST", "/api/admin/audit/export"): ADMIN,
    ("GET", "/api/admin/filings"): ADMIN,
    ("POST", "/api/admin/filings"): ADMIN,
    ("GET", "/api/admin/filings/{filing_id}"): ADMIN,
    ("PATCH", "/api/admin/filings/{filing_id}"): ADMIN,
    ("PUT", "/api/admin/filings/{filing_id}/calendar/draft"): ADMIN,
    ("DELETE", "/api/admin/filings/{filing_id}/calendar/draft"): ADMIN,
    ("POST", "/api/admin/filings/{filing_id}/calendar/publish"): ADMIN,
    ("GET", "/api/admin/packages"): ADMIN,
    ("POST", "/api/admin/packages"): ADMIN,
    ("GET", "/api/admin/packages/{package_id}"): ADMIN,
    ("PATCH", "/api/admin/packages/{package_id}"): ADMIN,
    ("PUT", "/api/admin/packages/{package_id}/draft"): ADMIN,
    ("DELETE", "/api/admin/packages/{package_id}/draft"): ADMIN,
    ("POST", "/api/admin/packages/{package_id}/publish"): ADMIN,
    ("POST", "/api/admin/filings/preview"): ADMIN,
    ("GET", "/api/admin/holidays"): ADMIN,
    ("POST", "/api/admin/holidays"): ADMIN,
    ("DELETE", "/api/admin/holidays/{day}"): ADMIN,
    ("GET", "/api/admin/lead-times"): ADMIN,
    ("PUT", "/api/admin/lead-times"): ADMIN,
    ("GET", "/api/clients/options"): ADMIN_AND_STAFF,
    ("POST", "/api/clients"): ADMIN_AND_STAFF,
    ("GET", "/api/clients/{client_id}"): ADMIN_AND_STAFF,
    ("PUT", "/api/clients/{client_id}/contacts/{contact_id}/consent"): ADMIN_AND_STAFF,
    ("GET", "/api/clients"): ADMIN_AND_STAFF,
    ("PATCH", "/api/clients/{client_id}"): ADMIN_AND_STAFF,
    ("GET", "/api/clients/{client_id}/history"): ADMIN_AND_STAFF,
    ("GET", "/api/stages"): ADMIN_AND_STAFF,
    ("GET", "/api/tickets/{ticket}"): ADMIN_AND_STAFF,
    ("GET", "/api/clients/{client_id}/tickets"): ADMIN_AND_STAFF,
    ("POST", "/api/tickets/{ticket}/transition"): ADMIN_AND_STAFF,
    ("GET", "/api/auth/me"): EVERYONE,
    ("POST", "/api/auth/logout"): EVERYONE,
    ("POST", "/api/auth/change-password"): EVERYONE,
    ("POST", "/api/auth/mfa/recovery-codes"): ADMIN_AND_STAFF,
}
# Reachable while a temporary password is still waiting to be changed.
REACHABLE_WHILE_PASSWORD_PENDING = {
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/change-password"),
}
ROUTES = sorted(EXPECTED)
UNSAFE = [r for r in ROUTES if r[0] in {"POST", "PUT", "PATCH", "DELETE"}]


def url_for(path: str) -> str:
    # No such record: 404 once past the checks.
    return (
        path.replace("{user_id}", "999999")
        .replace("{filing_id}", "999999")
        .replace("{package_id}", "999999")
        .replace("{day}", "1999-01-01")
        .replace("{client_id}", "999999")
        .replace("{contact_id}", "999999")
        .replace("{ticket}", "TKT-999999")
    )


def call(client: TestClient, route: tuple[str, str], headers: dict[str, str] | None = None):  # type: ignore[no-untyped-def]
    method, path = route
    return client.request(method, url_for(path), json={}, headers=headers or {})


def passed_the_checks(status: int) -> bool:
    """401 and 403 are refusals. Anything else (404, 422, 204, 200...) got past authorization."""
    return status not in (401, 403)


@dataclass
class World:
    url: str
    app_settings_url: str

    def client(self) -> TestClient:
        return TestClient(
            create_app(make_settings(database_url=self.url)), raise_server_exceptions=False
        )

    def signed(
        self, email: str, role: Role, *, must_change: bool = False
    ) -> tuple[TestClient, Signed, int]:
        user_id = make_user(self.url, email, role, must_change=must_change)
        client = self.client()
        return client, sign_in_directly(self.url, client, user_id), user_id


@pytest.fixture
def world(migrated_db: str) -> World:
    return World(migrated_db, migrated_db)


# ---- the code agrees with the hand-written table ---------------------------------------------


def test_the_code_requires_exactly_the_roles_the_table_says() -> None:
    app = create_app(make_settings())
    actual = protected_routes(app)
    assert set(actual) == set(EXPECTED), set(actual) ^ set(EXPECTED)
    for route, roles in EXPECTED.items():
        assert actual[route] == roles, route


def test_public_routes_are_exactly_the_documented_ones() -> None:
    assert unprotected_routes(create_app(make_settings())) == PUBLIC


# ---- every route, every role -------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("route", ROUTES, ids=lambda r: f"{r[0]} {r[1]}")
def test_each_role_gets_in_only_where_the_table_allows(
    world: World, route: tuple[str, str], role: Role
) -> None:
    client, signed, _ = world.signed(f"{role.value}@firm.in", role)
    resp = call(client, route, signed.headers)
    if role in EXPECTED[route]:
        assert passed_the_checks(resp.status_code), (route, role, resp.status_code, resp.text)
    else:
        assert resp.status_code == 403, (route, role, resp.status_code)
        assert resp.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: f"{r[0]} {r[1]}")
def test_nobody_signed_in_is_refused_everywhere(world: World, route: tuple[str, str]) -> None:
    resp = call(world.client(), route)
    assert resp.status_code == 401


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: f"{r[0]} {r[1]}")
def test_a_temporary_password_blocks_everything_except_changing_it(
    world: World, route: tuple[str, str]
) -> None:
    client, signed, _ = world.signed("temp@firm.in", Role.STAFF, must_change=True)
    resp = call(client, route, signed.headers)
    if route in REACHABLE_WHILE_PASSWORD_PENDING:
        assert passed_the_checks(resp.status_code), (route, resp.status_code)
    else:
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "password_change_required"


# ---- every route, every kind of dead session ---------------------------------------------------


def _kill_disabled(url: str, user_id: int) -> None:
    with db_session(url) as db:
        db.execute(update(User).where(User.id == user_id).values(is_active=False))
        db.commit()


def _kill_revoked(url: str, user_id: int) -> None:
    with db_session(url) as db:
        db.execute(update(UserSession).values(revoked_at=utcnow()))
        db.commit()


def _kill_idle(url: str, user_id: int) -> None:
    with db_session(url) as db:
        db.execute(update(UserSession).values(last_seen_at=utcnow() - timedelta(minutes=21)))
        db.commit()


def _kill_expired(url: str, user_id: int) -> None:
    with db_session(url) as db:
        db.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()


DEAD = {
    "disabled": _kill_disabled,
    "revoked": _kill_revoked,
    "idle for 21 minutes": _kill_idle,
    "past the 12-hour limit": _kill_expired,
}


@pytest.mark.parametrize("how", list(DEAD))
@pytest.mark.parametrize("route", ROUTES, ids=lambda r: f"{r[0]} {r[1]}")
def test_dead_sessions_are_refused_everywhere(
    world: World, route: tuple[str, str], how: str
) -> None:
    # An Administrator, so that every route would otherwise let this person in.
    client, signed, user_id = world.signed("boss@firm.in", Role.ADMINISTRATOR)
    DEAD[how](world.url, user_id)
    resp = call(client, route, signed.headers)
    assert resp.status_code == 401, (route, how, resp.status_code)


@pytest.mark.parametrize("route", ROUTES, ids=lambda r: f"{r[0]} {r[1]}")
def test_a_made_up_cookie_is_refused_everywhere(world: World, route: tuple[str, str]) -> None:
    client = world.client()
    client.cookies.set("ll_session", secrets.token_urlsafe(32))
    assert call(client, route, {"X-CSRF-Token": "x"}).status_code == 401


# ---- forged requests ---------------------------------------------------------------------------


@pytest.mark.parametrize("route", UNSAFE, ids=lambda r: f"{r[0]} {r[1]}")
def test_every_state_changing_route_needs_the_csrf_token(
    world: World, route: tuple[str, str]
) -> None:
    allowed = sorted(EXPECTED[route], key=lambda r: r.value)[0]
    client, signed, _ = world.signed(f"{allowed.value}@firm.in", allowed)
    for headers in ({}, {"X-CSRF-Token": "not-the-token"}, {"X-CSRF-Token": signed.csrf[::-1]}):
        resp = call(client, route, headers)
        assert resp.status_code == 403, (route, headers)
        assert resp.json()["error"]["code"] == "csrf_rejected"
    assert passed_the_checks(call(client, route, signed.headers).status_code)


@pytest.mark.parametrize("route", UNSAFE, ids=lambda r: f"{r[0]} {r[1]}")
def test_every_state_changing_route_refuses_a_foreign_origin(
    world: World, route: tuple[str, str]
) -> None:
    allowed = sorted(EXPECTED[route], key=lambda r: r.value)[0]
    client, signed, _ = world.signed(f"{allowed.value}@firm.in", allowed)
    resp = call(client, route, {**signed.headers, "Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "bad_origin"


# ---- the BRD matrix is a checklist that cannot silently shrink ----------------------------------


def test_all_sixteen_brd_capabilities_are_tracked() -> None:
    assert len(CAPABILITIES) == 16
    assert len({c.name for c in CAPABILITIES}) == 16
    for cap in CAPABILITIES:
        assert re.fullmatch(r"C\d\d", cap.built_in), cap.name


def test_built_capabilities_use_exactly_the_roles_the_matrix_gives_them() -> None:
    """A route may serve several capabilities (one search serves "all clients" and "assigned
    clients only"); its roles must then be exactly the union of theirs."""
    claimants: dict[tuple[str, str], list[Capability]] = {}
    for cap in CAPABILITIES:
        for route in cap.routes:
            claimants.setdefault(route, []).append(cap)
    for route, caps in claimants.items():
        roles = EXPECTED[route]
        names = [c.name for c in caps]
        # Consultants are allowed only once their chunk is built.
        assert (Role.CONSULTANT in roles) is any(c.consultant != "-" for c in caps), (names, route)
        assert (Role.STAFF in roles) is any(c.allowed("staff") for c in caps), (names, route)
        assert (Role.ADMINISTRATOR in roles) is any(c.allowed("admin") for c in caps), (
            names,
            route,
        )


def test_every_route_belongs_to_a_capability_or_is_self_service_or_public() -> None:
    claimed = {r for cap in CAPABILITIES for r in cap.routes} | set(SELF_SERVICE) | set(PUBLIC)
    app = create_app(make_settings())
    actual = set(protected_routes(app)) | unprotected_routes(app)
    assert actual == claimed, ("unclaimed or missing:", actual ^ claimed)


def test_capabilities_not_built_yet_have_no_routes_and_name_their_chunk() -> None:
    pending = [c for c in CAPABILITIES if not c.routes]
    assert len(pending) == 10  # shrinks as chunks land; update with the chunk that adds routes
    assert {c.built_in for c in pending} >= {"C16", "C21", "C28"}
    assert not {"C04", "C05", "C07", "C08"} & {c.built_in for c in pending}
