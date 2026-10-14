"""Application settings, read from environment (prefix LL_) and the repo-root .env.

Fail fast: the app refuses to start with weak or contradictory security settings.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from email_validator import EmailNotValidError, validate_email
from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url

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
    # The account that runs migrations (owns the tables and the audit triggers). The app's own
    # account must not have DDL rights, or it could drop the triggers that keep the audit
    # trail append-only. Required in production; falls back to database_url elsewhere.
    migration_database_url: SecretStr | None = None
    secret_key: SecretStr
    allowed_origins: Annotated[list[str], NoDecode] = []
    allowed_hosts: Annotated[list[str], NoDecode] = ["localhost", "127.0.0.1"]
    max_body_bytes: int = 1_048_576
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Email (BRD INT-01). Development sends to Mailpit on 127.0.0.1:1025 (scripts/dev-up).
    firm_name: str = "Ledgerline"
    email_from_address: str = "no-reply@ledgerline.example.com"  # a reserved domain
    email_from_name: str = "Ledgerline"
    smtp_host: str = "127.0.0.1"
    smtp_port: int = 1025
    smtp_security: Literal["none", "starttls", "tls"] = "none"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_seconds: int = 20
    # Signs delivery reports posted to /api/email/events. Unset: that endpoint is switched off.
    email_webhook_secret: SecretStr | None = None

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _csv(cls, value: object) -> object:
        return _split_csv(value)

    @field_validator("secret_key", "email_webhook_secret")
    @classmethod
    def _strong_secret(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        raw = value.get_secret_value()
        if len(raw) < MIN_SECRET_LENGTH:
            raise ValueError(f"secrets must be at least {MIN_SECRET_LENGTH} characters")
        if len(set(raw)) < MIN_DISTINCT_CHARS:
            raise ValueError("secret looks repetitive; generate one with `openssl rand -hex 32`")
        return value

    @field_validator("database_url", "migration_database_url")
    @classmethod
    def _mysql_only(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith("mysql+pymysql://"):
            raise ValueError("database urls must use the mysql+pymysql:// driver")
        return value

    @field_validator("email_from_address")
    @classmethod
    def _plain_address(cls, value: str) -> str:
        try:
            return validate_email(value, check_deliverability=False).normalized
        except EmailNotValidError as exc:
            raise ValueError(f"email_from_address is not a valid address: {exc}") from None

    @field_validator("firm_name", "email_from_name")
    @classmethod
    def _one_line(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or len(value) > 80:
            raise ValueError("names must be 1 to 80 characters on one line")
        return value

    @field_validator("smtp_timeout_seconds")
    @classmethod
    def _sane_timeout(cls, value: int) -> int:
        if not 1 <= value <= 120:
            raise ValueError("smtp_timeout_seconds must be 1 to 120")
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
            local_relay = self.smtp_host in {"127.0.0.1", "localhost", "::1"}
            if self.smtp_security == "none" and not local_relay:
                raise ValueError(
                    "production smtp_security must be starttls or tls unless smtp_host is a "
                    "relay on this machine"
                )
            domain = self.email_from_address.rsplit("@", 1)[-1].lower()
            if domain == "example.com" or domain.endswith(".example.com"):
                raise ValueError("production needs a real email_from_address")
            if self.migration_database_url is None:
                raise ValueError("production needs migration_database_url (a separate account)")
            app_user = make_url(self.database_url.get_secret_value()).username
            owner = make_url(self.migration_database_url.get_secret_value()).username
            if app_user == owner:
                raise ValueError(
                    "production database_url and migration_database_url must use different "
                    "accounts, so the app cannot alter the audit trail's protections"
                )
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def migration_url(self) -> str:
        chosen = self.migration_database_url or self.database_url
        return chosen.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
