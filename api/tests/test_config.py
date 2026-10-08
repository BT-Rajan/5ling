from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.conftest import make_settings


def test_valid_settings_load() -> None:
    s = make_settings()
    assert s.allowed_origins == ["https://app.example.test"]
    assert s.allowed_hosts == ["testserver", "localhost"]


@pytest.mark.parametrize(
    "secret", ["short", "9f2c6a1be07d4835a6c1f0e29b7d3c5", "k" * 64, "ab" * 32]
)
def test_weak_secret_rejected(secret: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(secret_key=secret)


def test_wildcard_origin_and_host_rejected() -> None:
    with pytest.raises(ValidationError):
        make_settings(allowed_origins="*")
    with pytest.raises(ValidationError):
        make_settings(allowed_hosts="*.example.test")


def test_database_must_be_mysql() -> None:
    with pytest.raises(ValidationError):
        make_settings(database_url="postgresql://u:p@h/db")
    with pytest.raises(ValidationError):
        make_settings(database_url="sqlite:///x.db")


def test_production_requires_https_and_real_hosts() -> None:
    with pytest.raises(ValidationError):
        make_settings(
            env="production",
            allowed_origins="http://app.example.test",
            allowed_hosts="app.example.test",
        )
    with pytest.raises(ValidationError):
        make_settings(
            env="production", allowed_origins="https://app.example.test", allowed_hosts="localhost"
        )
    ok = make_settings(
        env="production",
        allowed_origins="https://app.example.test",
        allowed_hosts="app.example.test",
    )
    assert ok.is_production


def test_secrets_do_not_appear_in_repr() -> None:
    s = make_settings()
    text = repr(s) + str(s)
    assert "9f2c6a1be07d4835a6c1f0e29b7d3c58e41a0f6b" not in text
    assert "nothing" not in text  # database password
