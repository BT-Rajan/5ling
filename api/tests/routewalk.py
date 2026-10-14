"""Find every API route and the roles each one demands, looking inside included routers."""

from __future__ import annotations

from app.models import Role
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

# Routes that need no session. Adding one means editing this set on purpose.
PUBLIC = frozenset(
    {
        ("GET", "/api/health/live"),
        ("GET", "/api/health/ready"),
        ("POST", "/api/auth/login"),
        # Second step of sign-in: they accept a single-use challenge, not a session.
        ("POST", "/api/auth/mfa/verify"),
        ("POST", "/api/auth/mfa/enrol/start"),
        ("POST", "/api/auth/mfa/enrol/confirm"),
        # Delivery reports from the email provider: signed with a shared secret instead.
        ("POST", "/api/email/events"),
        # The client portal: a private link or the portal session it started, not an account.
        ("POST", "/api/portal/open"),
        ("GET", "/api/portal/me"),
        ("POST", "/api/portal/logout"),
        ("POST", "/api/portal/request-link"),
        ("GET", "/api/portal/checklist"),
        ("POST", "/api/uploads/portal/items/{item_id}"),
    }
)


def roles_required(dependant: Dependant) -> list[frozenset[Role]]:
    found: list[frozenset[Role]] = []
    for dep in dependant.dependencies:
        marker = getattr(dep.call, "__authz__", None)
        if marker is not None:
            found.append(marker)
        found.extend(roles_required(dep))
    return found


def api_routes(app: FastAPI) -> list[APIRoute]:
    """All API routes (FastAPI wraps included routers in lazy objects, so look inside them)."""
    found: list[APIRoute] = []

    def visit(routes: list) -> None:  # type: ignore[type-arg]
        for route in routes:
            if isinstance(route, APIRoute):
                found.append(route)
            elif hasattr(route, "original_router"):
                visit(route.original_router.routes)

    visit(app.routes)
    # A walk that finds nothing would pass every check by accident, so insist on a sane count.
    paths = {r.path for r in found}
    assert len(found) >= 14, f"route walk found only {len(found)} routes"
    assert {"/api/auth/login", "/api/admin/users", "/api/health/live"} <= paths
    return found


def method_paths(route: APIRoute) -> set[tuple[str, str]]:
    return {(m, route.path) for m in route.methods or set()}


def protected_routes(app: FastAPI) -> dict[tuple[str, str], frozenset[Role]]:
    """Every route that needs a session, with the roles it accepts."""
    result: dict[tuple[str, str], frozenset[Role]] = {}
    for route in api_routes(app):
        markers = roles_required(route.dependant)
        if markers:
            union = frozenset().union(*markers)
            for key in method_paths(route):
                result[key] = union
    return result


def unprotected_routes(app: FastAPI) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for route in api_routes(app):
        if not roles_required(route.dependant):
            out |= method_paths(route)
    return out
