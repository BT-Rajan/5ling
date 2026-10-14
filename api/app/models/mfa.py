from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, UTCDateTime
from app.timeutil import utcnow

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


class ChallengePurpose(enum.StrEnum):
    VERIFY = "verify"  # password was right; now give the code
    ENROL = "enrol"  # password was right; no authenticator yet, so set one up
    EMAIL_LINK = "email_link"  # a consultant's emailed link; finished with the code on screen


class LoginChallenge(Base):
    """What a correct password earns: a short-lived, single-use right to attempt the second step.

    It is not a session. Nothing but the second-step endpoints accepts it.
    """

    __tablename__ = "login_challenges"
    __table_args__ = _TABLE

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    purpose: Mapped[ChallengePurpose] = mapped_column(
        Enum(ChallengePurpose, values_callable=lambda e: [m.value for m in e], name="purpose"),
        nullable=False,
    )
    pending_secret_enc: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # EMAIL_LINK: keyed hash of the code shown on the screen that asked for the link.
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class RecoveryCode(Base):
    """One-time codes for when the phone is lost. Only keyed hashes are stored."""

    __tablename__ = "recovery_codes"
    __table_args__ = (Index("ix_recovery_codes_user_id_used_at", "user_id", "used_at"), _TABLE)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
