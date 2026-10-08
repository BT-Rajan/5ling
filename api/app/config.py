"""Application settings, read from environment (prefix LL_) and the repo-root .env.

Fail fast: the app refuses to start with weak or contradictory security settings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

MIN_SECRET_LENGTH = 32
MIN_DISTINCT_CHARS = 12


def _split_csv(value: object) -> object:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LL_",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: Literal["development", "test", "production"] = "development"
    database_url: SecretStr
    secret_key: SecretStr
    allowed_origins: Annotated[list[str], NoDecode] = []
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1"]
    max_body_bytes: int = 1_048_576
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("secret_key")
    @classmethod
    def _strong_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < MIN_SECRET_LENGTH:
            raise ValueError(f"secret_key must be at least {MIN_SECRET_LENGTH} characters")
        if len(set(raw)) < MIN_DISTINCT_CHARS:
            raise ValueError(
                "secret_key looks repetitive; generate one with `openssl rand -hex 32`"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def _mysql_only(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("mysql+pymysql://"):
            raise ValueError("database_url must use the mysql+pymysql:// driver")
        return value

    @field_validator("max_body_bytes")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_body_bytes must be positive")
        return value

    @model_validator(mode="after")
    def _no_wildcards_and_prod_rules(self) -> Settings:
        if any("*" in origin for origin in self.allowed_origins):
            raise ValueError("allowed_origins must list exact origins, no wildcards")
        if any("*" in host for host in self.allowed_hosts):
            raise ValueError("allowed_hosts must list exact hosts, no wildcards")
        if self.env == "production":
            if not self.allowed_hosts or "localhost" in self.allowed_hosts:
                raise ValueError("production needs explicit public allowed_hosts")
            insecure = [o for o in self.allowed_origins if not o.startswith("https://")]
            if insecure:
                raise ValueError("production allowed_origins must all be https://")
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
