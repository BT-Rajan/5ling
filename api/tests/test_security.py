from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from app.logging_conf import RedactingJsonFormatter
from app.main import create_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import make_settings

REQUIRED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "cross-origin-opener-policy": "same-origin",
    "cross-origin-resource-policy": "same-origin",
    "cache-control": "no-store",
}


def assert_secure_headers(headers: dict[str, str]) -> None:
    for name, value in REQUIRED.items():
        assert headers.get(name) == value, name
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert "server" not in headers


def test_headers_on_success(client: TestClient) -> None:
    assert_secure_headers(dict(client.get("/api/health/live").headers))


def test_headers_on_404_and_422(client: TestClient) -> None:
    assert_secure_headers(dict(client.get("/api/nope").headers))
    assert_secure_headers(
        dict(client.get("/api/health/live", headers={"Host": "evil.test"}).headers)
    )


def test_headers_and_generic_body_on_unhandled_error(app: FastAPI) -> None:
    @app.get("/api/boom")
    def boom() -> None:
        raise RuntimeError("secret detail ABCDE1234F")

    resp = TestClient(app, raise_server_exceptions=False).get("/api/boom")
    assert resp.status_code == 500
    assert_secure_headers(dict(resp.headers))
    body = resp.json()["error"]
    assert body["code"] == "internal_error"
    assert "secret detail" not in resp.text
    assert "ABCDE1234F" not in resp.text
    assert body["request_id"] == resp.headers["x-request-id"]


def test_hsts_only_in_production() -> None:
    prod = create_app(make_settings(env="production", allowed_hosts="app.example.test"))
    resp = TestClient(prod, base_url="https://app.example.test").get("/api/health/live")
    assert "max-age=31536000" in resp.headers["strict-transport-security"]
    dev = TestClient(create_app(make_settings())).get("/api/health/live")
    assert "strict-transport-security" not in dev.headers


def test_docs_disabled_in_production_enabled_otherwise() -> None:
    prod = TestClient(
        create_app(make_settings(env="production", allowed_hosts="app.example.test")),
        base_url="https://app.example.test",
    )
    assert prod.get("/api/docs").status_code == 404
    assert prod.get("/api/openapi.json").status_code == 404
    dev = TestClient(create_app(make_settings(env="development")))
    assert dev.get("/api/docs").status_code == 200


def test_unknown_host_rejected(client: TestClient) -> None:
    resp = client.get("/api/health/live", headers={"Host": "evil.test"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_host"


def test_cors_allows_only_listed_origin(client: TestClient) -> None:
    good = client.get("/api/health/live", headers={"Origin": "https://app.example.test"})
    assert good.headers["access-control-allow-origin"] == "https://app.example.test"
    assert good.headers["access-control-allow-credentials"] == "true"
    bad = client.get("/api/health/live", headers={"Origin": "https://evil.test"})
    assert "access-control-allow-origin" not in bad.headers
    pre = client.options(
        "/api/health/live",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in pre.headers


def test_body_limit_by_content_length(client: TestClient) -> None:
    resp = client.post("/api/health/live", content=b"x" * 1_048_577)
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"
    assert_secure_headers(dict(resp.headers))


def test_body_limit_when_length_is_not_declared() -> None:
    app = create_app(make_settings(max_body_bytes=1000))

    @app.post("/api/echo")
    async def echo(payload: dict[str, str]) -> dict[str, str]:
        return payload

    def chunks() -> Iterator[bytes]:
        for _ in range(5):
            yield b'{"a":"' + b"x" * 300 + b'"}'

    resp = TestClient(app).post("/api/echo", content=chunks())
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "payload_too_large"


def test_validation_errors_never_echo_input(app: FastAPI) -> None:
    @app.post("/api/pan")
    def pan(payload: dict[str, int]) -> dict[str, int]:
        return payload

    resp = TestClient(app).post("/api/pan", json={"pan": "ABCDE1234F"})
    assert resp.status_code == 422
    assert "ABCDE1234F" not in resp.text
    assert resp.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize("sent", ["abc", "x" * 100, "bad id!", "../../etc"])
def test_untrusted_request_ids_are_replaced(client: TestClient, sent: str) -> None:
    resp = client.get("/api/health/live", headers={"X-Request-Id": sent})
    assert resp.headers["x-request-id"] != sent


def test_good_request_id_is_kept(client: TestClient) -> None:
    resp = client.get("/api/health/live", headers={"X-Request-Id": "req-12345678"})
    assert resp.headers["x-request-id"] == "req-12345678"


def test_access_log_has_no_query_string(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        client.get("/api/health/live?token=SUPERSECRETTOKEN123")
    records = [r for r in caplog.records if r.name == "ledgerline.http"]
    assert records
    line = RedactingJsonFormatter().format(records[0])
    assert "SUPERSECRETTOKEN123" not in line
    assert "/api/health/live" in line
