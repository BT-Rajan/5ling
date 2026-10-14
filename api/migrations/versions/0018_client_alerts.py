"""automatic client alerts and reminders; following up failed client emails (FR-AL-01/03/06)

Revision ID: 0018
Revises: 0017

cycle_alerts: one row per alert, at most one per cycle per day, only ever added (triggers).
emails: who followed up a failed or delayed client email, and when.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "cycle_alerts_no_update": (
        "CREATE TRIGGER cycle_alerts_no_update BEFORE UPDATE ON cycle_alerts FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'alerts are a record and are not changed'"
    ),
    "cycle_alerts_no_delete": (
        "CREATE TRIGGER cycle_alerts_no_delete BEFORE DELETE ON cycle_alerts FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'alerts are a record and are not changed'"
    ),
}

_KINDS_BEFORE = ("test", "audit_alert", "client_update", "portal_link")
_KINDS = (*_KINDS_BEFORE, "client_alert")


def upgrade() -> None:
    op.alter_column(
        "emails",
        "kind",
        existing_type=mysql.ENUM(*_KINDS_BEFORE),
        type_=sa.Enum(*_KINDS, name="email_kind"),
        existing_nullable=False,
    )
    op.add_column("emails", sa.Column("problem_handled_at", mysql.DATETIME(fsp=6), nullable=True))
    op.add_column("emails", sa.Column("problem_handled_by_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_emails_problem_handled_by_id_users"),
        "emails",
        "users",
        ["problem_handled_by_id"],
        ["id"],
    )
    op.create_table(
        "cycle_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Enum("first", "reminder", name="alert_kind"), nullable=False),
        sa.Column("sent_on", sa.Date(), nullable=False),
        sa.Column("email_id", sa.BigInteger(), nullable=False),
        sa.Column("portal_link_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_cycle_alerts_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["email_id"], ["emails.id"], name=op.f("fk_cycle_alerts_email_id_emails")
        ),
        sa.ForeignKeyConstraint(
            ["portal_link_id"],
            ["portal_links.id"],
            name=op.f("fk_cycle_alerts_portal_link_id_portal_links"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycle_alerts")),
        sa.UniqueConstraint("cycle_id", "sent_on", name=op.f("uq_cycle_alerts_cycle_id_sent_on")),
        sa.UniqueConstraint("email_id", name=op.f("uq_cycle_alerts_email_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("cycle_alerts")
    op.drop_constraint(op.f("fk_emails_problem_handled_by_id_users"), "emails", type_="foreignkey")
    op.drop_column("emails", "problem_handled_by_id")
    op.drop_column("emails", "problem_handled_at")
    op.alter_column(
        "emails",
        "kind",
        existing_type=sa.Enum(*_KINDS, name="email_kind"),
        type_=mysql.ENUM(*_KINDS_BEFORE),
        existing_nullable=False,
    )
