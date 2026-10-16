"""Per-client filing changes (C37; FR-PK-07).

Revision ID: 0032
Revises: 0031
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "client_filing_changes_kept": (
        "CREATE TRIGGER client_filing_changes_kept BEFORE DELETE ON client_filing_changes"
        " FOR EACH ROW SIGNAL SQLSTATE '45000'"
        " SET MESSAGE_TEXT = 'filing changes are never deleted'"
    ),
    "client_filing_changes_fixed": (
        "CREATE TRIGGER client_filing_changes_fixed BEFORE UPDATE ON client_filing_changes"
        " FOR EACH ROW BEGIN"
        " IF OLD.status <> 'pending' OR NEW.status = 'pending'"
        " OR NEW.client_pk <> OLD.client_pk OR NEW.filing_id <> OLD.filing_id"
        " OR NEW.kind <> OLD.kind OR NEW.reason <> OLD.reason"
        " OR NEW.requested_by_id <> OLD.requested_by_id OR NEW.requested_at <> OLD.requested_at"
        " THEN SIGNAL SQLSTATE '45000'"
        " SET MESSAGE_TEXT = 'a filing change can only be decided once';"
        " END IF; END"
    ),
}


def upgrade() -> None:
    op.create_table(
        "client_filing_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Enum("add", "remove", name="change_kind"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="change_status"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)"
            " AND (decided_at IS NULL) = (decided_by_id IS NULL)",
            name=op.f("ck_client_filing_changes_decided_with_whom"),
        ),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_client_filing_changes_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name=op.f("fk_client_filing_changes_decided_by_id_users"),
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"], ["filings.id"], name=op.f("fk_client_filing_changes_filing_id_filings")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name=op.f("fk_client_filing_changes_requested_by_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_filing_changes")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_client_filing_changes_client_pk_id",
        "client_filing_changes",
        ["client_pk", "id"],
        unique=False,
    )
    op.create_index(
        "ix_client_filing_changes_status", "client_filing_changes", ["status"], unique=False
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("client_filing_changes")
