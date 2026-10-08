from __future__ import annotations

import pytest
from app.main import create_app
from fastapi.testclient import TestClient

from tests.conftest import make_settings


def test_live(client: TestClient) -> None:
    resp = client.get("/api/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_ready_reports_unavailable_without_leaking_details(client: TestClient) -> None:
    resp = client.get("/api/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["database"] == "fail"
    assert "nobody" not in resp.text  # db user from the URL
    assert "127.0.0.1" not in resp.text


@pytest.mark.requires_db
def test_ready_on_migrated_database(clean_db: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.attributes["url"] = clean_db
    command.upgrade(cfg, "head")
    app = create_app(make_settings(database_url=clean_db))
    with TestClient(app) as c:
        assert c.get("/api/health/ready").json() == {"status": "ok", "checks": {"database": "ok"}}


@pytest.mark.requires_db
def test_ready_says_not_migrated_on_empty_database(clean_db: str) -> None:
    app = create_app(make_settings(database_url=clean_db))
    with TestClient(app) as c:
        resp = c.get("/api/health/ready")
        assert resp.status_code == 503
        assert resp.json()["checks"]["database"] in {"fail", "not_migrated"}
