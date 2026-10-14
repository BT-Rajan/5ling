"""in-app notifications and escalations (FR-IN-01 to FR-IN-03)

Revision ID: 0019
Revises: 0018

Triggers: notifications are never deleted; what one says and who it is for never change; it
is read once and dismissed once; an escalation cannot be dismissed without a note.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "notifications_kept": (
        "CREATE TRIGGER notifications_kept BEFORE DELETE ON notifications FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'notifications are never deleted'"
    ),
    "notifications_fixed": (
        "CREATE TRIGGER notifications_fixed BEFORE UPDATE ON notifications FOR EACH ROW BEGIN "
        "IF NEW.kind <> OLD.kind OR NOT (NEW.user_id <=> OLD.user_id)"
        " OR NOT (NEW.for_role <=> OLD.for_role) OR NEW.client_pk <> OLD.client_pk"
        " OR NOT (NEW.cycle_id <=> OLD.cycle_id) OR NEW.title <> OLD.title"
        " OR NEW.body <> OLD.body OR NEW.dedupe_key <> OLD.dedupe_key"
        " OR NEW.created_at <> OLD.created_at"
        " OR (OLD.read_at IS NOT NULL AND NOT (NEW.read_at <=> OLD.read_at))"
        " OR (OLD.dismissed_at IS NOT NULL AND (NOT (NEW.dismissed_at <=> OLD.dismissed_at)"
        " OR NOT (NEW.dismissed_by_id <=> OLD.dismissed_by_id)"
        " OR NOT (NEW.dismiss_note <=> OLD.dismiss_note))) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a notification cannot be changed that way'; "
        "END IF; "
        "IF NEW.kind = 'escalation' AND NEW.dismissed_at IS NOT NULL"
        " AND CHAR_LENGTH(TRIM(COALESCE(NEW.dismiss_note, ''))) < 3 THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'an escalation needs a note to dismiss'; "
        "END IF; END"
    ),
}


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "kind",
            sa.Enum("documents_received", "waiting_on_client", "escalation", name="kind"),
            nullable=False,
        ),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "for_role",
            sa.Enum("administrator", "staff", "consultant", name="for_role"),
            nullable=True,
        ),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("dedupe_key", sa.String(length=120), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("read_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("dismissed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("dismissed_by_id", sa.BigInteger(), nullable=True),
        sa.Column("dismiss_note", sa.String(length=1000), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_notifications_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_notifications_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["dismissed_by_id"], ["users.id"], name=op.f("fk_notifications_dismissed_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_notifications_dedupe_key")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_notifications_for_role_dismissed_at_id",
        "notifications",
        ["for_role", "dismissed_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_id_dismissed_at_id",
        "notifications",
        ["user_id", "dismissed_at", "id"],
        unique=False,
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("notifications")
