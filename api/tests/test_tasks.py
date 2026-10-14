"""Chunk C11: ad hoc task tickets and the ticket list (CR-02, CR-03)."""

from __future__ import annotations

from typing import Any

import pytest
from app.models import Role, StateEvent
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_clients import _ok, admin, client_body, create, publish_package

__all__ = ["admin"]  # the fixture, re-used from test_clients

pytestmark = pytest.mark.requires_db


def staff_session(web: TestClient, url: str, email: str, name: str) -> tuple[TestClient, Signed]:
    user_id = make_user(url, email, Role.STAFF, name=name)
    client = TestClient(web.app)
    return client, sign_in_directly(url, client, user_id)


def new_task(web: TestClient, signed: Signed, client_id: int, **over: Any) -> Any:
    body: dict[str, Any] = {"client_id": client_id, "title": "Collect signed engagement letter"}
    body.update(over)
    return web.post("/api/tickets", json=body, headers=signed.headers)


def codes(page: Any) -> list[str]:
    return [row["code"] for row in page["items"]]


# --- Done when: a manual task gets a TKT ID, an assignee and a due date, and appears in lists -


def test_a_task_gets_an_id_an_assignee_and_a_due_date_and_is_listed(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    owner, owner_s = staff_session(web, migrated_db, "owner@firm.in", "Owner Person")
    helper, helper_s = staff_session(web, migrated_db, "helper@firm.in", "Helper Person")
    cid = _ok(create(web, admin, client_body(pid, owner_id=owner_s.user_id)), 201)["client_id"]

    made = _ok(
        new_task(
            owner,
            owner_s,
            cid,
            assignee_id=helper_s.user_id,
            due_date="2026-11-20",
            description="Bring the original",
        ),
        201,
    )
    code = made["code"]
    assert code.startswith("TKT-")
    assert made["type"] == "task"
    assert made["task"]["assignee"] == {"id": helper_s.user_id, "full_name": "Helper Person"}
    assert (made["task"]["due_date"], made["task"]["status"]) == ("2026-11-20", "open")

    # Listed for the owner, the assignee and the Administrator, and findable by its ID.
    for who in (owner, helper, web):
        listed = _ok(who.get("/api/tickets", params={"type": "task"}))
        assert codes(listed) == [code]
        row = listed["items"][0]
        assert (row["state"], row["state_label"], row["due_date"]) == ("open", "Open", "2026-11-20")
    assert codes(_ok(web.get("/api/tickets", params={"q": code}))) == [code]
    # "TKT…" finds that ticket only; a bare number also finds titles containing it.
    assert codes(_ok(web.get("/api/tickets", params={"q": code.replace("-", "").lower()}))) == [
        code
    ]
    bare = codes(
        _ok(web.get("/api/tickets", params={"q": code.split("-")[1].lstrip("0"), "limit": 100}))
    )
    assert code in bare
    assert codes(_ok(web.get("/api/tickets", params={"assignee_id": helper_s.user_id}))) == [code]
    assert code in [t["code"] for t in _ok(owner.get(f"/api/clients/{cid}/tickets"))]
    # Filings and tasks share the list, soonest due first.
    everything = _ok(web.get("/api/tickets", params={"client_id": cid, "limit": 100}))
    assert {r["type"] for r in everything["items"]} == {"filing", "task"}
    dues = [r["due_date"] for r in everything["items"]]
    assert dues == sorted(dues)


# --- The assignee ----------------------------------------------------------------------------


def test_the_assignee_sees_only_the_task_and_can_only_change_its_status(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    helper, helper_s = staff_session(web, migrated_db, "helper@firm.in", "Helper Person")
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    code = _ok(new_task(web, admin, cid, assignee_id=helper_s.user_id), 201)["code"]

    seen = _ok(helper.get(f"/api/tickets/{code}"))
    assert seen["can_edit"] is False
    assert helper.get(f"/api/clients/{cid}").status_code == 403  # not the client
    assert helper.get(f"/api/clients/{cid}/tickets").status_code == 403
    filing = _ok(web.get("/api/tickets", params={"client_id": cid, "type": "filing"}))["items"][0][
        "code"
    ]
    assert helper.get(f"/api/tickets/{filing}").status_code == 403  # nor its other tickets
    assert codes(_ok(helper.get("/api/tickets"))) == [code]

    edit = helper.patch(
        f"/api/tickets/{code}", json={"title": "Something else"}, headers=helper_s.headers
    )
    assert edit.status_code == 403
    done = _ok(
        helper.patch(
            f"/api/tickets/{code}",
            json={"status": "done", "expected_status": "open"},
            headers=helper_s.headers,
        )
    )
    assert (done["task"]["status"], done["task"]["completed_at"] is not None) == ("done", True)
    twice = helper.patch(
        f"/api/tickets/{code}",
        json={"status": "done", "expected_status": "open"},
        headers=helper_s.headers,
    )
    assert twice.json()["error"]["code"] == "status_changed"
    reopened = _ok(
        helper.patch(f"/api/tickets/{code}", json={"status": "open"}, headers=helper_s.headers)
    )
    assert reopened["task"]["completed_at"] is None

    with db_session(migrated_db) as db:
        rows = list(db.scalars(select(StateEvent).order_by(StateEvent.id)))
    assert [(e.kind.value, e.from_state, e.to_state) for e in rows] == [
        ("task_status", "open", "done"),
        ("task_status", "done", "open"),
    ]
    # Reassigned away, the task is closed to them from the next request.
    _ok(
        web.patch(
            f"/api/tickets/{code}", json={"assignee_id": admin.user_id}, headers=admin.headers
        )
    )
    assert helper.get(f"/api/tickets/{code}").status_code == 403


# --- Rules -----------------------------------------------------------------------------------


def test_who_may_create_and_what_a_task_needs(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    other, other_s = staff_session(web, migrated_db, "other@firm.in", "Other Person")
    consultant = make_user(migrated_db, "c@ca.in", Role.CONSULTANT)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    assert new_task(other, other_s, cid).status_code == 403  # not their client
    assert (
        new_task(web, admin, cid, assignee_id=consultant).json()["error"]["code"] == "bad_assignee"
    )
    assert new_task(web, admin, cid, title=" ").status_code == 422
    assert new_task(web, admin, cid, due_date="1999-01-01").json()["error"]["code"] == "bad_date"
    assert new_task(web, admin, 999999).status_code == 404
    unassigned = _ok(new_task(web, admin, cid), 201)
    assert unassigned["task"]["assignee"] is None


def test_filings_and_tasks_keep_to_their_own_kind_of_change(web: TestClient, admin: Signed) -> None:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    task = _ok(new_task(web, admin, cid), 201)["code"]
    filing = _ok(web.get("/api/tickets", params={"type": "filing", "client_id": cid}))["items"][0][
        "code"
    ]
    patched = web.patch(f"/api/tickets/{filing}", json={"title": "x"}, headers=admin.headers)
    assert patched.json()["error"]["code"] == "not_a_task"
    moved = web.post(
        f"/api/tickets/{task}/transition",
        json={"action": "request_documents", "expected_stage": "scheduled"},
        headers=admin.headers,
    )
    assert moved.json()["error"]["code"] == "not_a_cycle"
    assert (
        web.patch(f"/api/tickets/{task}", json={}, headers=admin.headers).json()["error"]["code"]
        == "no_change"
    )


def test_list_filters_and_staff_visibility(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    pid = publish_package(web, admin)
    mine, mine_s = staff_session(web, migrated_db, "mine@firm.in", "Mine Person")
    cid_mine = _ok(create(web, admin, client_body(pid, owner_id=mine_s.user_id)), 201)["client_id"]
    cid_theirs = _ok(
        create(web, admin, client_body(pid, owner_id=admin.user_id, pan="ABCPF1234G")), 201
    )["client_id"]
    open_task = _ok(new_task(web, admin, cid_theirs), 201)["code"]
    done_task = _ok(new_task(web, admin, cid_theirs, title="Done already"), 201)["code"]
    _ok(web.patch(f"/api/tickets/{done_task}", json={"status": "done"}, headers=admin.headers))

    assert codes(_ok(web.get("/api/tickets", params={"type": "task", "open_only": True}))) == [
        open_task
    ]
    assert codes(_ok(web.get("/api/tickets", params={"type": "task", "state": "done"}))) == [
        done_task
    ]
    assert _ok(web.get("/api/tickets", params={"q": "already"}))["items"][0]["code"] == done_task
    # Staff see their own clients' tickets only.
    theirs = _ok(mine.get("/api/tickets", params={"limit": 100}))
    assert {r["client"]["client_id"] for r in theirs["items"]} == {cid_mine}
    assert _ok(mine.get("/api/tickets", params={"client_id": cid_theirs}))["total"] == 0
    stage = _ok(mine.get("/api/tickets", params={"state": "scheduled"}))
    assert stage["total"] == theirs["total"]
