"""Chunk C14: private client links and the portal shell (BRD FR-AL-02, NFR-US-01)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from app.models import Email, EmailKind, EmailStatus, PortalLink, Role
from app.portal import links as portal_links
from app.timeutil import utcnow
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text

from tests.audit_helpers import events
from tests.helpers import Signed, db_session, make_user, sign_in_directly
from tests.test_clients import _ok, admin, client_body, create, publish_package
from tests.test_tickets import first_ticket

__all__ = ["admin"]  # the fixture, re-used from test_clients

pytestmark = pytest.mark.requires_db


def token_of(url: str) -> str:
    assert "/portal#t=" in url
    return url.split("#t=", 1)[1]


def setup_cycle(web: TestClient, admin: Signed, url: str) -> tuple[int, str]:
    pid = publish_package(web, admin)
    cid = _ok(create(web, admin, client_body(pid, owner_id=admin.user_id)), 201)["client_id"]
    return cid, first_ticket(url, cid)


def new_link(web: TestClient, signed: Signed, code: str, **body: Any) -> Any:
    return web.post(f"/api/tickets/{code}/links", json=body, headers=signed.headers)


def visitor(web: TestClient) -> TestClient:
    """A browser with no staff session: the client."""
    return TestClient(web.app)


def link_emails(url: str) -> list[Email]:
    with db_session(url) as db:
        return list(
            db.scalars(select(Email).where(Email.kind == EmailKind.PORTAL_LINK).order_by(Email.id))
        )


# --- Done when: an expired or reused link shows a clear message and offers a new link; nothing is
# visible without a valid link -------------------------------------------------------------------


def test_a_link_opens_once_and_a_reused_one_offers_a_new_link(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, code = setup_cycle(web, admin, migrated_db)
    made = _ok(new_link(web, admin, code, send_email=True), 201)
    assert made["url"].startswith("http://localhost:5173/portal#t=")
    assert made["emailed_to"] == "a•••@example.in"
    assert made["link"]["status"] == "waiting"
    token = token_of(made["url"])
    sent = link_emails(migrated_db)
    assert len(sent) == 1
    assert (sent[0].to_addresses, sent[0].status) == (["asha@example.in"], EmailStatus.QUEUED)
    assert made["url"] in sent[0].body_text
    assert code in sent[0].subject

    client = visitor(web)
    assert client.get("/api/portal/me").status_code == 401  # nothing without the link
    opened = _ok(client.post("/api/portal/open", json={"token": token}))
    assert (opened["client_id"], opened["ticket"], opened["stage_label"]) == (
        cid,
        code,
        "Scheduled",
    )
    cookie = client.cookies.get("ll_portal")
    assert cookie
    assert cookie != token  # the session is not the link
    assert _ok(client.get("/api/portal/me"))["ticket"] == code

    # The same link again, from anywhere: refused, with a way to ask for a new one.
    again = visitor(web).post("/api/portal/open", json={"token": token})
    assert again.status_code == 410
    error = again.json()["error"]
    assert (error["code"], error["can_request_new"]) == ("link_used", True)
    assert "already been opened" in error["message"]

    asked = visitor(web).post("/api/portal/request-link", json={"token": token})
    assert asked.status_code == 202
    assert asked.json() == {"sent_to": "a•••@example.in"}  # sent to the address on file only
    newest = link_emails(migrated_db)[-1]
    assert newest.to_addresses == ["asha@example.in"]
    twice = visitor(web).post("/api/portal/request-link", json={"token": token})
    assert twice.json()["error"]["code"] == "already_requested"
    fresh = newest.body_text.split("#t=", 1)[1].split()[0]
    assert _ok(visitor(web).post("/api/portal/open", json={"token": fresh}))["ticket"] == code

    listed = _ok(web.get(f"/api/tickets/{code}/links"))
    assert [(r["status"], r["origin"]) for r in listed] == [
        ("opened", "client_request"),
        ("opened", "staff"),
    ]
    trail = events(migrated_db)
    for event in ("portal.link_created", "portal.link_opened", "portal.link_refused"):
        assert event in trail
    assert "portal.link_requested" in trail


def test_an_expired_link_says_so_and_offers_a_new_one(
    web: TestClient, admin: Signed, migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, code = setup_cycle(web, admin, migrated_db)
    token = token_of(_ok(new_link(web, admin, code, send_email=False), 201)["url"])
    assert link_emails(migrated_db) == []
    later = utcnow() + timedelta(hours=73)
    monkeypatch.setattr(portal_links, "utcnow", lambda: later)
    expired = visitor(web).post("/api/portal/open", json={"token": token})
    assert expired.status_code == 410
    assert (expired.json()["error"]["code"], expired.json()["error"]["can_request_new"]) == (
        "link_expired",
        True,
    )
    assert visitor(web).post("/api/portal/request-link", json={"token": token}).status_code == 202


def test_nothing_opens_without_a_good_link(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup_cycle(web, admin, migrated_db)
    client = visitor(web)
    bad = client.post("/api/portal/open", json={"token": "not-a-real-link"})
    assert bad.status_code == 404
    assert bad.json()["error"]["can_request_new"] is False
    assert client.post("/api/portal/request-link", json={"token": "nope"}).status_code == 404
    client.cookies.set("ll_portal", "made-up-session")
    assert client.get("/api/portal/me").status_code == 401
    assert client.post("/api/portal/open", json={"token": ""}).status_code == 422
    assert client.post("/api/portal/open", json={"token": "x" * 101}).status_code == 422
    # A still-good link is not swapped for a new one.
    token = token_of(_ok(new_link(web, admin, code, send_email=False), 201)["url"])
    still = visitor(web).post("/api/portal/request-link", json={"token": token})
    assert still.json()["error"]["code"] == "link_still_valid"
    # Refusing an unknown token records nothing (anyone could fill the log that way).
    assert "portal.link_refused" not in events(migrated_db)


def test_a_new_link_replaces_older_unopened_ones(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup_cycle(web, admin, migrated_db)
    first = token_of(_ok(new_link(web, admin, code, send_email=False), 201)["url"])
    second = token_of(_ok(new_link(web, admin, code, send_email=False), 201)["url"])
    old = visitor(web).post("/api/portal/open", json={"token": first})
    assert old.json()["error"]["code"] == "link_withdrawn"
    assert _ok(visitor(web).post("/api/portal/open", json={"token": second}))["ticket"] == code
    statuses = [r["status"] for r in _ok(web.get(f"/api/tickets/{code}/links"))]
    assert statuses == ["opened", "replaced"]


def test_withdrawing_a_link_or_closing_the_client_ends_the_portal_at_once(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    cid, code = setup_cycle(web, admin, migrated_db)
    made = _ok(new_link(web, admin, code, send_email=False), 201)
    client = visitor(web)
    _ok(client.post("/api/portal/open", json={"token": token_of(made["url"])}))
    withdrawn = _ok(
        web.post(f"/api/tickets/{code}/links/{made['link']['id']}/withdraw", headers=admin.headers)
    )
    assert withdrawn["status"] == "ended"
    assert client.get("/api/portal/me").status_code == 401
    assert "portal.link_withdrawn" in events(migrated_db)

    other = _ok(new_link(web, admin, code, send_email=False), 201)
    client2 = visitor(web)
    _ok(client2.post("/api/portal/open", json={"token": token_of(other["url"])}))
    _ok(
        web.patch(
            f"/api/clients/{cid}",
            json={"status": "closed", "reason": "Engagement ended"},
            headers=admin.headers,
        )
    )
    assert client2.get("/api/portal/me").status_code == 401
    closed = visitor(web).post("/api/portal/open", json={"token": token_of(other["url"])})
    assert closed.json()["error"]["code"] in {"link_closed", "link_used"}
    assert new_link(web, admin, code).json()["error"]["code"] == "closed"


def test_signing_out_needs_the_portal_csrf_token(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup_cycle(web, admin, migrated_db)
    token = token_of(_ok(new_link(web, admin, code, send_email=False), 201)["url"])
    client = visitor(web)
    csrf = _ok(client.post("/api/portal/open", json={"token": token}))["csrf_token"]
    assert client.post("/api/portal/logout").status_code == 403
    assert client.get("/api/portal/me").status_code == 200
    assert client.post("/api/portal/logout", headers={"X-CSRF-Token": csrf}).status_code == 204
    assert client.get("/api/portal/me").status_code == 401


def test_only_the_owner_makes_links_and_requests_are_limited(
    web: TestClient, admin: Signed, migrated_db: str
) -> None:
    _, code = setup_cycle(web, admin, migrated_db)
    other_id = make_user(migrated_db, "other@firm.in", Role.STAFF)
    other = TestClient(web.app)
    signed = sign_in_directly(migrated_db, other, other_id)
    assert new_link(other, signed, code).status_code == 403
    assert other.get(f"/api/tickets/{code}/links").status_code == 403

    token = token_of(_ok(new_link(web, admin, code, send_email=False), 201)["url"])
    _ok(visitor(web).post("/api/portal/open", json={"token": token}))
    for _ in range(3):
        sent = link_emails(migrated_db)
        _ok(visitor(web).post("/api/portal/request-link", json={"token": token}), 202)
        newest = link_emails(migrated_db)[-1]
        assert len(link_emails(migrated_db)) == len(sent) + 1
        token = newest.body_text.split("#t=", 1)[1].split()[0]
        _ok(visitor(web).post("/api/portal/open", json={"token": token}))
    capped = visitor(web).post("/api/portal/request-link", json={"token": token})
    assert capped.status_code == 429


def test_links_cannot_be_rewritten(web: TestClient, admin: Signed, migrated_db: str) -> None:
    _, code = setup_cycle(web, admin, migrated_db)
    token = token_of(_ok(new_link(web, admin, code, send_email=False), 201)["url"])
    _ok(visitor(web).post("/api/portal/open", json={"token": token}))
    with db_session(migrated_db) as db:
        row = db.scalar(select(PortalLink))
        assert row is not None
        assert row.token_hash != token  # only a hash is kept
        assert len(row.token_hash) == 64
    engine = create_engine(migrated_db)
    try:
        for sql, message in (
            ("UPDATE portal_links SET used_at = NULL", "cannot be changed"),
            (
                "UPDATE portal_links SET expires_at = expires_at + INTERVAL 1 DAY",
                "cannot be changed",
            ),
            ("UPDATE portal_links SET token_hash = REPEAT('a', 64)", "cannot be changed"),
            ("DELETE FROM portal_links", "never deleted"),
        ):
            with pytest.raises(Exception, match=message), engine.begin() as conn:
                conn.execute(text(sql))
    finally:
        engine.dispose()
