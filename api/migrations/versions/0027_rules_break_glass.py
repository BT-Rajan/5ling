"""rules as data (NFR-MN-01) and break-glass grants (FR-AC-05)

Revision ID: 0027
Revises: 0026

rule_settings: one row of timings an Administrator edits (grace period, reminder interval,
escalation point, link expiry, idle timeout, break-glass length), loaded with the defaults that
were settings until now; never deleted. break_glass_grants: never changed or deleted.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "rule_settings_kept": (
        "CREATE TRIGGER rule_settings_kept BEFORE DELETE ON rule_settings FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'the rules are never deleted'"
    ),
    "break_glass_grants_kept": (
        "CREATE TRIGGER break_glass_grants_kept BEFORE DELETE ON break_glass_grants FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'break-glass grants are never deleted'"
    ),
    "break_glass_grants_fixed": (
        "CREATE TRIGGER break_glass_grants_fixed BEFORE UPDATE ON break_glass_grants FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'break-glass grants are never changed'"
    ),
}


def upgrade() -> None:
    op.create_table(
        "rule_settings",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("waiting_grace_days", sa.Integer(), nullable=False),
        sa.Column("reminder_interval_days", sa.Integer(), nullable=False),
        sa.Column("escalation_days_before", sa.Integer(), nullable=False),
        sa.Column("portal_link_hours", sa.Integer(), nullable=False),
        sa.Column("idle_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("break_glass_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "break_glass_minutes BETWEEN 15 AND 1440",
            name=op.f("ck_rule_settings_break_glass_minutes_range"),
        ),
        sa.CheckConstraint(
            "escalation_days_before BETWEEN 0 AND 30",
            name=op.f("ck_rule_settings_escalation_days_before_range"),
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_rule_settings_single_row")),
        sa.CheckConstraint(
            "idle_timeout_minutes BETWEEN 5 AND 240",
            name=op.f("ck_rule_settings_idle_timeout_minutes_range"),
        ),
        sa.CheckConstraint(
            "portal_link_hours BETWEEN 1 AND 720",
            name=op.f("ck_rule_settings_portal_link_hours_range"),
        ),
        sa.CheckConstraint(
            "reminder_interval_days BETWEEN 1 AND 14",
            name=op.f("ck_rule_settings_reminder_interval_days_range"),
        ),
        sa.CheckConstraint(
            "waiting_grace_days BETWEEN 0 AND 30",
            name=op.f("ck_rule_settings_waiting_grace_days_range"),
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], name=op.f("fk_rule_settings_updated_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_settings")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "break_glass_grants",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("granted_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "expires_at > granted_at", name=op.f("ck_break_glass_grants_expires_after_granted")
        ),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_break_glass_grants_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_break_glass_grants_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_break_glass_grants")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_break_glass_grants_user_id_client_pk_expires_at",
        "break_glass_grants",
        ["user_id", "client_pk", "expires_at"],
        unique=False,
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)
    op.execute(
        "INSERT INTO rule_settings (id, waiting_grace_days, reminder_interval_days,"
        " escalation_days_before, portal_link_hours, idle_timeout_minutes, break_glass_minutes,"
        " updated_at) VALUES (1, 3, 2, 2, 72, 20, 240, UTC_TIMESTAMP(6))"
    )


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("break_glass_grants")
    op.drop_table("rule_settings")
