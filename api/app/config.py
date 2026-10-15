"""Application settings, read from environment (prefix LL_) and the repo-root .env.

Fail fast: the app refuses to start with weak or contradictory security settings.
"""

from __future__ import annotations

import base64
import binascii
import re
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

    # Client portal links (BRD FR-AL-02): where the web app is. How long a link lasts, the
    # reminder spacing, grace period, escalation point and idle timeout are rules an
    # Administrator edits (rule_settings, C29), not settings.
    public_url: str = "http://localhost:5173"

    # Client alerts (BRD FR-AL-01): the hours (India time) in which automatic emails go out.
    alert_hours_start: int = 9
    alert_hours_end: int = 19

    # Documents (BRD INT-03, INT-04, NFR-SE-04). Files are encrypted before they are written, with
    # a key kept apart from the database: 32 random bytes, base64 (openssl rand -base64 32).
    storage_dir: Path = ROOT / ".dev" / "storage"
    storage_key: SecretStr | None = None  # required in production; development derives one
    max_upload_bytes: int = 20 * 1024 * 1024
    clamd_address: str = "unix:/var/run/clamav/clamd.ctl"  # or tcp:127.0.0.1:3310

    # Fees (BRD FR-FE-02, FR-FE-09). Invoice numbers read PREFIX/YY-YY/00001 (at most 16
    # characters, as GST requires). The firm's GST state code (the first two digits of its GSTIN)
    # decides CGST and SGST (same state) or IGST (a client registered in another state).
    invoice_prefix: str = "INV"
    invoice_terms_days: int = 15
    firm_gst_state: str | None = None

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

    @field_validator("invoice_prefix")
    @classmethod
    def _prefix(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Z]{1,4}", value):
            raise ValueError("invoice_prefix must be 1 to 4 capital letters, e.g. INV")
        if value == "CN":
            raise ValueError("invoice_prefix CN is kept for credit notes")
        return value

    @field_validator("invoice_terms_days")
    @classmethod
    def _terms(cls, value: int) -> int:
        if not 0 <= value <= 180:
            raise ValueError("invoice_terms_days must be 0 to 180")
        return value

    @field_validator("firm_gst_state")
    @classmethod
    def _state(cls, value: str | None) -> str | None:
        if value in (None, ""):
            return None
        if not re.fullmatch(r"\d{2}", value) or not (1 <= int(value) <= 38 or value == "97"):
            raise ValueError("firm_gst_state must be a two-digit GST state code, e.g. 33")
        return value

    @field_validator("firm_name", "email_from_name")
    @classmethod
    def _one_line(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value or len(value) > 80:
            raise ValueError("names must be 1 to 80 characters on one line")
        return value

    @field_validator("public_url")
    @classmethod
    def _base_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")) or any(c in value for c in "?#@ "):
            raise ValueError("public_url must be a plain http(s) address, e.g. https://app.firm.in")
        return value

    @field_validator("storage_key")
    @classmethod
    def _storage_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                raw = base64.b64decode(value.get_secret_value(), validate=True)
            except (binascii.Error, ValueError):
                raw = b""
            if len(raw) != 32:
                raise ValueError("storage_key must be 32 random bytes, base64-encoded")
        return value

    @field_validator("alert_hours_start", "alert_hours_end")
    @classmethod
    def _hour(cls, value: int) -> int:
        if not 0 <= value <= 24:
            raise ValueError("alert hours must be 0 to 24")
        return value

    @field_validator("max_upload_bytes")
    @classmethod
    def _upload_limit(cls, value: int) -> int:
        if not 1024 <= value <= 200 * 1024 * 1024:
            raise ValueError("max_upload_bytes must be 1 KB to 200 MB")
        return value

    @field_validator("clamd_address")
    @classmethod
    def _clamd(cls, value: str) -> str:
        if not value.startswith(("unix:/", "tcp:")):
            raise ValueError("clamd_address must be unix:/path or tcp:host:port")
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
        if self.alert_hours_start >= self.alert_hours_end:
            raise ValueError("alert_hours_start must be before alert_hours_end")
        if self.env == "production":
            if not self.allowed_hosts or "localhost" in self.allowed_hosts:
                raise ValueError("production needs explicit public allowed_hosts")
            insecure = [o for o in self.allowed_origins if not o.startswith("https://")]
            if insecure:
                raise ValueError("production allowed_origins must all be https://")
            if self.storage_key is None:
                raise ValueError("production needs storage_key (kept apart from the database)")
            if not self.public_url.startswith("https://"):
                raise ValueError("production public_url must be https://")
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
