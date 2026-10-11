"""Authorization: no route is open by accident, and record-level rules behave as the BRD says."""

from __future__ import annotations

import logging
from typing import cast

import pytest
from app.auth.access import RecordAccess, authorize_record
from app.auth.deps import Principal, require_roles
from app.main import create_app
from app.models import Role
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from tests.conftest import make_settings
from tests.helpers import make_user, sign_in, sign_in_directly
from tests.routewalk import PUBLIC, api_routes, roles_required, unprotected_routes

ANY_ROLE = require_roles(Role.ADMINISTRATOR, Role.STAFF, Role.CONSULTANT)


@pytest.mark.parametrize("env", ["test", "production"])
def test_every_route_is_public_on_purpose_or_requires_a_role(env: str) -> None:
    extra = {"allowed_hosts": "app.example.test"} if env == "production" else {}
    app = create_app(make_settings(env=env, **extra))
    assert unprotected_routes(app) == PUBLIC


def test_every_admin_route_is_limited_to_the_administrator() -> None:
    app = create_app(make_settings())
    admin_routes = [r for r in api_routes(app) if r.path.startswith("/api/admin/")]
    assert admin_routes
    for route in admin_routes:
        assert roles_required(route.dependant) == [frozenset({Role.ADMINISTRATOR})], route.path


def _principal(role: Role, user_id: int = 1) -> Principal:
    return Principal(user_id, "x@firm.in", "X", role, 1, "c", False)


def test_administrator_may_open_any_record() -> None:
    authorize_record(_principal(Role.ADMINISTRATOR), RecordAccess(owner_id=99), what="client")


def test_staff_may_open_only_their_own() -> None:
    authorize_record(_principal(Role.STAFF, 5), RecordAccess(owner_id=5), what="client")
    with pytest.raises(Exception, match="403") as info:
        authorize_record(_principal(Role.STAFF, 5), RecordAccess(owner_id=6), what="client")
    assert info.value.status_code == 403  # type: ignore[attr-defined]


def test_consultant_may_open_only_what_is_assigned_and_learns_nothing_otherwise() -> None:
    mine = RecordAccess(owner_id=1, assignee_ids=frozenset({7}))
    authorize_record(_principal(Role.CONSULTANT, 7), mine, what="cycle")
    with pytest.raises(Exception, match="404") as info:
        authorize_record(_principal(Role.CONSULTANT, 8), mine, what="cycle")
    assert info.value.status_code == 404  # type: ignore[attr-defined]


def test_a_missing_record_is_404_for_everyone() -> None:
    for role in Role:
        with pytest.raises(Exception, match="404"):
            authorize_record(_principal(role), None, what="client")


def test_denials_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="ledgerline.security"), pytest.raises(Exception):  # noqa: B017, PT011
        authorize_record(_principal(Role.STAFF, 5), RecordAccess(owner_id=6), what="client")
    assert any(getattr(r, "ctx", {}).get("event") == "access.denied" for r in caplog.records)


@pytest.mark.requires_db
def test_reassigning_a_record_moves_access_at_once(web: TestClient, migrated_db: str) -> None:
    """BRD FR-AC-03 pattern: after reassignment the previous owner sees 'no access' next request."""
    owners = {"client-1": 0}
    assignees: dict[str, set[int]] = {"client-1": set()}

    app = cast("FastAPI", web.app)

    @app.get("/api/test-records/{record_id}")
    def record(
        record_id: str,
        principal: Principal = Depends(ANY_ROLE),  # noqa: B008
    ) -> dict[str, str]:
        access = (
            RecordAccess(owners[record_id], frozenset(assignees[record_id]))
            if record_id in owners
            else None
        )
        authorize_record(principal, access, what=record_id)
        return {"ok": record_id}

    asha = make_user(migrated_db, "asha@firm.in", Role.STAFF)
    ravi = make_user(migrated_db, "ravi@firm.in", Role.STAFF)
    cons = make_user(migrated_db, "cons@firm.in", Role.CONSULTANT)
    boss = make_user(migrated_db, "boss@firm.in", Role.ADMINISTRATOR)
    owners["client-1"] = asha

    with (
        TestClient(web.app) as a,
        TestClient(web.app) as r,
        TestClient(web.app) as c,
        TestClient(web.app) as b,
    ):
        sign_in(a, "asha@firm.in")
        sign_in(r, "ravi@firm.in")
        sign_in_directly(migrated_db, c, cons)
        sign_in(b, "boss@firm.in")
        url = "/api/test-records/client-1"

        assert a.get(url).status_code == 200
        assert r.get(url).status_code == 403
        assert c.get(url).status_code == 404
        assert b.get(url).status_code == 200
        assert a.get("/api/test-records/nope").status_code == 404

        owners["client-1"] = ravi  # reassigned
        assert a.get(url).status_code == 403  # previous owner: no access on the very next request
        assert r.get(url).status_code == 200

        assignees["client-1"].add(cons)  # assigned to the consultant
        assert c.get(url).status_code == 200
        assignees["client-1"].clear()  # assignment withdrawn
        assert c.get(url).status_code == 404
    assert boss
