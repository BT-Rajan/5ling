"""WhatsApp and SMS messages (C36; INT-02, FR-AL-08, C-2).

Revision ID: 0031
Revises: 0030
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_NOTE_KINDS = (
    "documents_received",
    "waiting_on_client",
    "escalation",
    "assigned",
    "no_activity",
    "phone_promise",
    "client_message",
)

_TRIGGERS = {
    "channel_messages_kept": (
        "CREATE TRIGGER channel_messages_kept BEFORE DELETE ON channel_messages FOR EACH ROW"
        " SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'channel messages are never deleted'"
    ),
    # What was sent, to whom, never changes; a message's outcome is recorded once.
    "channel_messages_fixed": (
        "CREATE TRIGGER channel_messages_fixed BEFORE UPDATE ON channel_messages FOR EACH ROW BEGIN"
        " IF OLD.status <> 'queued' OR NEW.client_pk <> OLD.client_pk"
        " OR NOT (NEW.cycle_id <=> OLD.cycle_id) OR NEW.contact_id <> OLD.contact_id"
        " OR NOT (NEW.email_id <=> OLD.email_id) OR NEW.channel <> OLD.channel"
        " OR NEW.template <> OLD.template OR NEW.body_text <> OLD.body_text"
        " OR NOT (NEW.params <=> OLD.params)"
        " OR NEW.created_at <> OLD.created_at THEN"
        " SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'channel messages are never changed';"
        " END IF; END"
    ),
}


def upgrade() -> None:
    op.alter_column(
        "notifications",
        "kind",
        existing_type=mysql.ENUM(*_NOTE_KINDS),
        type_=sa.Enum(*_NOTE_KINDS, "channel_failed", name="kind"),
        existing_nullable=False,
    )
    op.create_table(
        "channel_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=True),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("email_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "channel", sa.Enum("email", "whatsapp", "sms", name="message_channel"), nullable=False
        ),
        sa.Column("template", sa.String(length=40), nullable=False),
        sa.Column("body_text", sa.String(length=1000), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("queued", "sent", "failed", "skipped", name="message_status"),
            nullable=False,
        ),
        sa.Column("attempts", sa.SmallInteger(), nullable=False),
        sa.Column("provider_id", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_channel_messages_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["client_contacts.id"],
            name=op.f("fk_channel_messages_contact_id_client_contacts"),
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_channel_messages_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["email_id"], ["emails.id"], name=op.f("fk_channel_messages_email_id_emails")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_channel_messages")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_channel_messages_status_id", "channel_messages", ["status", "id"], unique=False
    )
    op.create_index("ix_channel_messages_cycle_id", "channel_messages", ["cycle_id"], unique=False)
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("channel_messages")
    # Notifications are never deleted, so this fails while any channel_failed ones exist.
    op.alter_column(
        "notifications",
        "kind",
        existing_type=mysql.ENUM(*_NOTE_KINDS, "channel_failed"),
        type_=sa.Enum(*_NOTE_KINDS, name="kind"),
        existing_nullable=False,
    )
