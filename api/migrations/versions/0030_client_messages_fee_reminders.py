"""Client messages, the completion link, and fee reminders (C35; FR-AL-07, FR-WF-06, FR-FE-07).

Revision ID: 0030
Revises: 0029
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_EMAIL_KINDS = (
    "test",
    "audit_alert",
    "client_update",
    "portal_link",
    "client_alert",
    "consultant_sign_in",
    "assignment",
)
_NOTE_KINDS = (
    "documents_received",
    "waiting_on_client",
    "escalation",
    "assigned",
    "no_activity",
    "phone_promise",
)

_TRIGGERS = {
    f"{table}_{what}": (
        f"CREATE TRIGGER {table}_{what} BEFORE {event} ON {table} FOR EACH ROW"
        f" SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{noun} are never {verb}'"
    )
    for table, noun in (("client_messages", "client messages"), ("fee_reminders", "fee reminders"))
    for what, event, verb in (("kept", "DELETE", "deleted"), ("fixed", "UPDATE", "changed"))
}


def upgrade() -> None:
    op.alter_column(
        "emails",
        "kind",
        existing_type=mysql.ENUM(*_EMAIL_KINDS),
        type_=sa.Enum(*_EMAIL_KINDS, "fee_reminder", name="email_kind"),
        existing_nullable=False,
    )
    op.alter_column(
        "notifications",
        "kind",
        existing_type=mysql.ENUM(*_NOTE_KINDS),
        type_=sa.Enum(*_NOTE_KINDS, "client_message", name="kind"),
        existing_nullable=False,
    )
    op.add_column(
        "rule_settings",
        sa.Column("fee_reminder_interval_days", sa.Integer(), nullable=False, server_default="7"),
    )
    op.alter_column(
        "rule_settings",
        "fee_reminder_interval_days",
        existing_type=sa.Integer(),
        existing_nullable=False,
        server_default=None,
    )
    op.create_check_constraint(
        op.f("ck_rule_settings_fee_reminder_interval_days_range"),
        "rule_settings",
        "fee_reminder_interval_days BETWEEN 1 AND 60",
    )
    op.create_table(
        "client_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("portal_link_id", sa.BigInteger(), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_client_messages_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_client_messages_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["portal_link_id"],
            ["portal_links.id"],
            name=op.f("fk_client_messages_portal_link_id_portal_links"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_messages")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_client_messages_cycle_id_id", "client_messages", ["cycle_id", "id"], unique=False
    )
    op.create_table(
        "fee_reminders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("email_id", sa.BigInteger(), nullable=False),
        sa.Column("sent_on", sa.Date(), nullable=False),
        sa.Column("balance_paise", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["email_id"], ["emails.id"], name=op.f("fk_fee_reminders_email_id_emails")
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_fee_reminders_invoice_id_invoices")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fee_reminders")),
        sa.UniqueConstraint(
            "invoice_id", "sent_on", name=op.f("uq_fee_reminders_invoice_id_sent_on")
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("fee_reminders")
    op.drop_table("client_messages")
    op.drop_constraint(
        op.f("ck_rule_settings_fee_reminder_interval_days_range"), "rule_settings", type_="check"
    )
    op.drop_column("rule_settings", "fee_reminder_interval_days")
    # Emails and notifications are never deleted, so this fails while any of the new kinds exist.
    op.alter_column(
        "notifications",
        "kind",
        existing_type=mysql.ENUM(*_NOTE_KINDS, "client_message"),
        type_=sa.Enum(*_NOTE_KINDS, name="kind"),
        existing_nullable=False,
    )
    op.alter_column(
        "emails",
        "kind",
        existing_type=mysql.ENUM(*_EMAIL_KINDS, "fee_reminder"),
        type_=sa.Enum(*_EMAIL_KINDS, name="email_kind"),
        existing_nullable=False,
    )
