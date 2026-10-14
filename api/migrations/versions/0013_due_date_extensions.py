"""due-date extensions and the cycle dates each one changed (FR-PK-06)

Revision ID: 0013
Revises: 0012

Both tables are append-only (triggers): an extension and what it changed stay on record.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Literal statements (no string building), one refusal per table and operation.
_TRIGGERS = {
    "due_date_extensions_no_update": (
        "CREATE TRIGGER due_date_extensions_no_update BEFORE UPDATE ON due_date_extensions "
        "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'extensions are append-only'"
    ),
    "due_date_extensions_no_delete": (
        "CREATE TRIGGER due_date_extensions_no_delete BEFORE DELETE ON due_date_extensions "
        "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'extensions are append-only'"
    ),
    "due_date_changes_no_update": (
        "CREATE TRIGGER due_date_changes_no_update BEFORE UPDATE ON due_date_changes "
        "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'due date history is append-only'"
    ),
    "due_date_changes_no_delete": (
        "CREATE TRIGGER due_date_changes_no_delete BEFORE DELETE ON due_date_changes "
        "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'due date history is append-only'"
    ),
}

_TABLE = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
    "mysql_engine": "InnoDB",
}


def upgrade() -> None:
    op.create_table(
        "due_date_extensions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_label", sa.String(length=40), nullable=False),
        sa.Column("new_due_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("cycles_changed", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_due_date_extensions_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"], ["filings.id"], name=op.f("fk_due_date_extensions_filing_id_filings")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_due_date_extensions")),
        **_TABLE,
    )
    op.create_index(
        "ix_due_date_extensions_filing_id_period_start",
        "due_date_extensions",
        ["filing_id", "period_start"],
        unique=False,
    )
    op.create_table(
        "due_date_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("extension_id", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("old_due_date", sa.Date(), nullable=False),
        sa.Column("old_alert_date", sa.Date(), nullable=False),
        sa.Column("new_due_date", sa.Date(), nullable=False),
        sa.Column("new_alert_date", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_due_date_changes_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["extension_id"],
            ["due_date_extensions.id"],
            name=op.f("fk_due_date_changes_extension_id_due_date_extensions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_due_date_changes")),
        sa.UniqueConstraint(
            "extension_id", "cycle_id", name=op.f("uq_due_date_changes_extension_id_cycle_id")
        ),
        **_TABLE,
    )
    op.create_index(
        "ix_due_date_changes_cycle_id_id", "due_date_changes", ["cycle_id", "id"], unique=False
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("due_date_changes")
    op.drop_table("due_date_extensions")
