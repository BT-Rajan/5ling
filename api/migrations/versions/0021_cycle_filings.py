"""filing proof: acknowledgement and reference number (FR-WF-05)

Revision ID: 0021
Revises: 0020

cycle_filings: one row per cycle, never changed or deleted. A cycle cannot be moved to Filed
unless its row exists (trigger on cycles), so no code path can file without proof.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "cycle_filings_kept": (
        "CREATE TRIGGER cycle_filings_kept BEFORE DELETE ON cycle_filings FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'filing proof is never deleted'"
    ),
    "cycle_filings_fixed": (
        "CREATE TRIGGER cycle_filings_fixed BEFORE UPDATE ON cycle_filings FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'filing proof is never changed'"
    ),
    "cycles_filed_with_proof": (
        "CREATE TRIGGER cycles_filed_with_proof BEFORE UPDATE ON cycles FOR EACH ROW BEGIN "
        "IF NEW.stage = 'filed' AND OLD.stage <> 'filed'"
        " AND NOT EXISTS (SELECT 1 FROM cycle_filings WHERE cycle_id = NEW.id) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a cycle cannot be filed without proof'; "
        "END IF; END"
    ),
}


def upgrade() -> None:
    op.create_table(
        "cycle_filings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("reference", sa.String(length=50), nullable=False),
        sa.Column("acknowledgement_version_id", sa.BigInteger(), nullable=False),
        sa.Column("filed_by_id", sa.BigInteger(), nullable=False),
        sa.Column("filed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["acknowledgement_version_id"],
            ["document_versions.id"],
            name=op.f("fk_cycle_filings_acknowledgement_version_id_document_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_cycle_filings_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["filed_by_id"], ["users.id"], name=op.f("fk_cycle_filings_filed_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycle_filings")),
        sa.UniqueConstraint("cycle_id", name=op.f("uq_cycle_filings_cycle_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("cycle_filings")
