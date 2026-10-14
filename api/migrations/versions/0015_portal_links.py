"""private client links (FR-AL-02), and portal-link emails

Revision ID: 0015
Revises: 0014

Triggers: a link's token, client, cycle and expiry never change; once opened it stays opened;
once withdrawn it stays withdrawn; links are never deleted.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "portal_links_fixed": (
        "CREATE TRIGGER portal_links_fixed BEFORE UPDATE ON portal_links FOR EACH ROW BEGIN "
        "IF NEW.token_hash <> OLD.token_hash OR NEW.client_pk <> OLD.client_pk"
        " OR NEW.cycle_id <> OLD.cycle_id OR NEW.expires_at <> OLD.expires_at"
        " OR NEW.created_at <> OLD.created_at OR NEW.origin <> OLD.origin"
        " OR NOT (NEW.created_by_id <=> OLD.created_by_id)"
        " OR NOT (NEW.requested_from_id <=> OLD.requested_from_id)"
        " OR (OLD.used_at IS NOT NULL AND NOT (NEW.used_at <=> OLD.used_at))"
        " OR (OLD.session_hash IS NOT NULL AND NOT (NEW.session_hash <=> OLD.session_hash))"
        " OR (OLD.revoked_at IS NOT NULL AND NOT (NEW.revoked_at <=> OLD.revoked_at)) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a link cannot be changed that way'; "
        "END IF; END"
    ),
    "portal_links_kept": (
        "CREATE TRIGGER portal_links_kept BEFORE DELETE ON portal_links FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'links are never deleted'"
    ),
}

_KINDS_BEFORE = ("test", "audit_alert", "client_update")
_KINDS = (*_KINDS_BEFORE, "portal_link")


def upgrade() -> None:
    op.create_table(
        "portal_links",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "origin",
            sa.Enum("staff", "client_request", "system", name="origin"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("used_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("session_hash", sa.String(length=64), nullable=True),
        sa.Column("last_seen_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("revoked_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("revoked_reason", sa.String(length=40), nullable=True),
        sa.Column("requested_from_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_portal_links_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_portal_links_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_portal_links_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["requested_from_id"],
            ["portal_links.id"],
            name=op.f("fk_portal_links_requested_from_id_portal_links"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portal_links")),
        sa.UniqueConstraint("requested_from_id", name=op.f("uq_portal_links_requested_from_id")),
        sa.UniqueConstraint("session_hash", name=op.f("uq_portal_links_session_hash")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_portal_links_token_hash")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_portal_links_cycle_id_id", "portal_links", ["cycle_id", "id"], unique=False)
    op.alter_column(
        "emails",
        "kind",
        existing_type=mysql.ENUM(*_KINDS_BEFORE),
        type_=sa.Enum(*_KINDS, name="email_kind"),
        existing_nullable=False,
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.alter_column(
        "emails",
        "kind",
        existing_type=sa.Enum(*_KINDS, name="email_kind"),
        type_=mysql.ENUM(*_KINDS_BEFORE),
        existing_nullable=False,
    )
    op.drop_table("portal_links")
