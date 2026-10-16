"""Nil filings and phone follow-up (C34; FR-CY-03, FR-IN-05).

Revision ID: 0029
Revises: 0028
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_KINDS_BEFORE = ("documents_received", "waiting_on_client", "escalation", "assigned")
_KINDS_ADDED = ("no_activity", "phone_promise")

_TRIGGERS = {
    "cycle_nil_confirmations_kept": (
        "CREATE TRIGGER cycle_nil_confirmations_kept BEFORE DELETE ON cycle_nil_confirmations"
        " FOR EACH ROW SIGNAL SQLSTATE '45000'"
        " SET MESSAGE_TEXT = 'nil confirmations are never deleted'"
    ),
    "cycle_nil_confirmations_fixed": (
        "CREATE TRIGGER cycle_nil_confirmations_fixed BEFORE UPDATE ON cycle_nil_confirmations"
        " FOR EACH ROW SIGNAL SQLSTATE '45000'"
        " SET MESSAGE_TEXT = 'nil confirmations are never changed'"
    ),
    "phone_contacts_kept": (
        "CREATE TRIGGER phone_contacts_kept BEFORE DELETE ON phone_contacts FOR EACH ROW"
        " SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'phone contacts are never deleted'"
    ),
    # Only the reminder outcome may be set, once; the note itself never changes.
    "phone_contacts_fixed": (
        "CREATE TRIGGER phone_contacts_fixed BEFORE UPDATE ON phone_contacts FOR EACH ROW BEGIN"
        " IF NEW.ticket_id <> OLD.ticket_id OR NEW.client_pk <> OLD.client_pk"
        " OR NEW.user_id <> OLD.user_id OR NEW.note <> OLD.note"
        " OR NOT (NEW.promised_on <=> OLD.promised_on) OR NEW.created_at <> OLD.created_at"
        " OR OLD.reminded_at IS NOT NULL THEN"
        " SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'phone contacts are never changed';"
        " END IF; END"
    ),
}


def upgrade() -> None:
    op.alter_column(
        "notifications",
        "kind",
        existing_type=mysql.ENUM(*_KINDS_BEFORE),
        type_=sa.Enum(*_KINDS_BEFORE, *_KINDS_ADDED, name="kind"),
        existing_nullable=False,
    )
    op.create_table(
        "cycle_nil_confirmations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("portal_link_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("confirmed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_cycle_nil_confirmations_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_cycle_nil_confirmations_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["portal_link_id"],
            ["portal_links.id"],
            name=op.f("fk_cycle_nil_confirmations_portal_link_id_portal_links"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycle_nil_confirmations")),
        sa.UniqueConstraint("cycle_id", name=op.f("uq_cycle_nil_confirmations_cycle_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "phone_contacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=False),
        sa.Column("promised_on", sa.Date(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("reminded_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("reminder_sent", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_phone_contacts_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_phone_contacts_ticket_id_tickets")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_phone_contacts_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phone_contacts")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_phone_contacts_ticket_id_id", "phone_contacts", ["ticket_id", "id"], unique=False
    )
    op.create_index(
        "ix_phone_contacts_due", "phone_contacts", ["reminded_at", "promised_on"], unique=False
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("phone_contacts")  # its indexes go with it
    op.drop_table("cycle_nil_confirmations")
    # Notifications are never deleted (0019), so this fails while any of the new kinds exist.
    op.alter_column(
        "notifications",
        "kind",
        existing_type=mysql.ENUM(*_KINDS_BEFORE, *_KINDS_ADDED),
        type_=sa.Enum(*_KINDS_BEFORE, name="kind"),
        existing_nullable=False,
    )
