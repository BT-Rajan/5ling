"""the email outbox and each email's status history (INT-01, FR-AL-06, CR-03)

Revision ID: 0014
Revises: 0013

Triggers: an email's status only moves forward; once it leaves draft its recipients and text are
final; emails are never deleted; the status history is append-only.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

_STATUSES = ("draft", "queued", "sent", "delivered", "failed", "bounced")

# Literal statements (no string building), so nothing here can be steered by data.
_TRIGGERS = {
    "emails_forward_only": (
        "CREATE TRIGGER emails_forward_only BEFORE UPDATE ON emails FOR EACH ROW BEGIN "
        "IF NOT (OLD.status = NEW.status"
        " OR (OLD.status = 'draft' AND NEW.status = 'queued')"
        " OR (OLD.status = 'queued' AND NEW.status IN ('sent', 'failed'))"
        " OR (OLD.status = 'sent' AND NEW.status IN ('delivered', 'bounced', 'failed'))"
        " OR (OLD.status = 'delivered' AND NEW.status = 'bounced')) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'email status cannot move that way'; "
        "END IF; "
        "IF NEW.public_id <> OLD.public_id OR NEW.kind <> OLD.kind"
        " OR NEW.created_at <> OLD.created_at OR NOT (NEW.created_by_id <=> OLD.created_by_id)"
        " OR NOT (NEW.client_pk <=> OLD.client_pk) OR NOT (NEW.ticket_id <=> OLD.ticket_id)"
        " OR NOT (NEW.state_event_id <=> OLD.state_event_id)"
        " OR (OLD.status <> 'draft' AND (NOT (NEW.to_addresses <=> OLD.to_addresses)"
        " OR NOT (NEW.cc_addresses <=> OLD.cc_addresses)"
        " OR NEW.subject <> OLD.subject OR NEW.body_text <> OLD.body_text)) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a queued email cannot be changed'; "
        "END IF; END"
    ),
    "emails_kept": (
        "CREATE TRIGGER emails_kept BEFORE DELETE ON emails FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'emails are never deleted'"
    ),
    "email_events_no_update": (
        "CREATE TRIGGER email_events_no_update BEFORE UPDATE ON email_events FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'email history is append-only'"
    ),
    "email_events_no_delete": (
        "CREATE TRIGGER email_events_no_delete BEFORE DELETE ON email_events FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'email history is append-only'"
    ),
}

_TABLE = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
    "mysql_engine": "InnoDB",
}


def upgrade() -> None:
    op.create_table(
        "emails",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("test", "audit_alert", "client_update", name="email_kind"),
            nullable=False,
        ),
        sa.Column("status", sa.Enum(*_STATUSES, name="email_status"), nullable=False),
        sa.Column("to_addresses", sa.JSON(), nullable=False),
        sa.Column("cc_addresses", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=250), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=True),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("state_event_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("queued_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("queued_by_id", sa.BigInteger(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("sent_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("delivered_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("failed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column("provider_message_id", sa.String(length=250), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_emails_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_emails_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["queued_by_id"], ["users.id"], name=op.f("fk_emails_queued_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["state_event_id"],
            ["state_events.id"],
            name=op.f("fk_emails_state_event_id_state_events"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_emails_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_emails")),
        sa.UniqueConstraint("public_id", name=op.f("uq_emails_public_id")),
        sa.UniqueConstraint("state_event_id", name=op.f("uq_emails_state_event_id")),
        **_TABLE,
    )
    op.create_index("ix_emails_client_pk_id", "emails", ["client_pk", "id"], unique=False)
    op.create_index("ix_emails_kind_created_at", "emails", ["kind", "created_at"], unique=False)
    op.create_index(
        "ix_emails_status_next_attempt_at", "emails", ["status", "next_attempt_at"], unique=False
    )
    op.create_index("ix_emails_ticket_id_id", "emails", ["ticket_id", "id"], unique=False)
    op.create_table(
        "email_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("email_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Enum(*_STATUSES, name="event_status"), nullable=False),
        sa.Column(
            "source", sa.Enum("app", "worker", "provider", name="event_source"), nullable=False
        ),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("occurred_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["email_id"], ["emails.id"], name=op.f("fk_email_events_email_id_emails")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_events")),
        **_TABLE,
    )
    op.create_index("ix_email_events_email_id_id", "email_events", ["email_id", "id"], unique=False)
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("email_events")
    op.drop_table("emails")
