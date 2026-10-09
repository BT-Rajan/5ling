from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    false,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class Role(enum.StrEnum):
    """People who have accounts. Clients act through private links, not accounts (chunk C14)."""

    ADMINISTRATOR = "administrator"
    STAFF = "staff"
    CONSULTANT = "consultant"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # BRD section 12: a consultant account needs a recorded confidentiality agreement date.
        CheckConstraint(
            "role <> 'consultant' OR agreement_signed_on IS NOT NULL",
            name="consultant_has_agreement",
        ),
        # Consultants sign in by emailed link and code (chunk C21), so they hold no password.
        CheckConstraint(
            "role = 'consultant' OR password_hash IS NOT NULL", name="login_roles_have_password"
        ),
        _TABLE,
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    role: Mapped[Role] = mapped_column(
        Enum(Role, values_callable=lambda e: [m.value for m in e], name="role"), nullable=False
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    agreement_signed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    failed_login_count: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class UserSession(Base):
    """A signed-in browser. Only a hash of the cookie value is stored."""

    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_user_id_revoked_at", "user_id", "revoked_at"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class LoginAttempt(Base):
    """Failed and successful sign-ins by address, used to slow down password guessing."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_ip_attempted_at", "ip", "attempted_at"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    attempted_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
