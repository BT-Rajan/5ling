"""users, sessions, login attempts

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("attempted_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("succeeded", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_login_attempts")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_login_attempts_ip_attempted_at", "login_attempts", ["ip", "attempted_at"], unique=False
    )
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column(
            "role", sa.Enum("administrator", "staff", "consultant", name="role"), nullable=False
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "must_change_password", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("agreement_signed_on", sa.Date(), nullable=True),
        sa.Column(
            "failed_login_count", sa.SmallInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("locked_until", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("last_login_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("password_changed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("disabled_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "role <> 'consultant' OR agreement_signed_on IS NOT NULL",
            name=op.f("ck_users_consultant_has_agreement"),
        ),
        sa.CheckConstraint(
            "role = 'consultant' OR password_hash IS NOT NULL",
            name=op.f("ck_users_login_roles_have_password"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name=op.f("fk_users_created_by_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("ip", sa.String(length=45), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("last_seen_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_sessions_token_hash")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_sessions_user_id_revoked_at", "sessions", ["user_id", "revoked_at"], unique=False
    )


def downgrade() -> None:
    # Dropping a table drops its indexes; MySQL refuses to drop one that backs a foreign key.
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("login_attempts")
