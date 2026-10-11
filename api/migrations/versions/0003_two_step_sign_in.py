"""two-step sign-in: TOTP columns, login challenges, recovery codes

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}


def upgrade() -> None:
    op.create_table(
        "login_challenges",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.Enum("verify", "enrol", name="purpose"), nullable=False),
        sa.Column("pending_secret_enc", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_login_challenges_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_challenges")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_login_challenges_token_hash")),
        **_TABLE,
    )
    op.create_index(
        op.f("ix_login_challenges_user_id"), "login_challenges", ["user_id"], unique=False
    )
    op.create_table(
        "recovery_codes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_recovery_codes_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recovery_codes")),
        **_TABLE,
    )
    op.create_index(
        "ix_recovery_codes_user_id_used_at", "recovery_codes", ["user_id", "used_at"], unique=False
    )
    op.add_column("users", sa.Column("totp_secret_enc", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("totp_enabled_at", mysql.DATETIME(fsp=6), nullable=True))
    op.add_column("users", sa.Column("totp_last_step", sa.BigInteger(), nullable=True))
    # Alembic's autogenerate does not detect CHECK constraints, so this one is added by hand.
    op.create_check_constraint(
        "totp_enabled_when_secret",
        "users",
        "totp_secret_enc IS NULL OR totp_enabled_at IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("totp_enabled_when_secret", "users", type_="check")
    op.drop_column("users", "totp_last_step")
    op.drop_column("users", "totp_enabled_at")
    op.drop_column("users", "totp_secret_enc")
    # Dropping a table drops its indexes; MySQL refuses to drop one that backs a foreign key.
    op.drop_table("recovery_codes")
    op.drop_table("login_challenges")
