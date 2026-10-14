"""client change history (FR-CL-04): append-only

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "client_changes_no_update": (
        "CREATE TRIGGER client_changes_no_update BEFORE UPDATE ON client_changes FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'client history is append-only'"
    ),
    "client_changes_no_delete": (
        "CREATE TRIGGER client_changes_no_delete BEFORE DELETE ON client_changes FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'client history is append-only'"
    ),
}


def upgrade() -> None:
    op.create_table(
        "client_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column(
            "field", sa.Enum("owner", "package", "status", name="change_field"), nullable=False
        ),
        sa.Column("old_value", sa.String(length=64), nullable=False),
        sa.Column("new_value", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("changed_by_id", sa.BigInteger(), nullable=False),
        sa.Column("changed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by_id"], ["users.id"], name=op.f("fk_client_changes_changed_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_client_changes_client_pk_clients")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_changes")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_client_changes_client_pk_id", "client_changes", ["client_pk", "id"], unique=False
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("client_changes")
