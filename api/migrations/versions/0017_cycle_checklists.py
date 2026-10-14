"""each cycle's own copy of its document checklist (FR-AL-04, FR-PK-05)

Revision ID: 0017
Revises: 0016

Existing cycles get the checklist of the package version they were built from. Triggers: an item
keeps its cycle, wording, order and whether it is required; its document is set once; items are
never deleted.
"""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "cycle_checklist_items_kept": (
        "CREATE TRIGGER cycle_checklist_items_kept BEFORE DELETE ON cycle_checklist_items "
        "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'checklist items are kept'"
    ),
    "cycle_checklist_items_fixed": (
        "CREATE TRIGGER cycle_checklist_items_fixed BEFORE UPDATE ON cycle_checklist_items "
        "FOR EACH ROW BEGIN "
        "IF NEW.cycle_id <> OLD.cycle_id OR NEW.position <> OLD.position"
        " OR NEW.label <> OLD.label OR NEW.required <> OLD.required"
        " OR (OLD.document_id IS NOT NULL AND NOT (NEW.document_id <=> OLD.document_id)) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a checklist item cannot be changed'; "
        "END IF; END"
    ),
}

# Every existing cycle: the checklist of its own package version and filing, in order.
_BACKFILL = (
    "INSERT INTO cycle_checklist_items (cycle_id, position, label, required) "
    "SELECT c.id, i.position, i.label, i.required FROM cycles c "
    "JOIN package_filings pf ON pf.package_version_id = c.package_version_id "
    "AND pf.filing_id = c.filing_id "
    "JOIN package_checklist_items i ON i.package_filing_id = pf.id"
)


def upgrade() -> None:
    op.create_table(
        "cycle_checklist_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_cycle_checklist_items_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_cycle_checklist_items_document_id_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycle_checklist_items")),
        sa.UniqueConstraint(
            "cycle_id", "position", name=op.f("uq_cycle_checklist_items_cycle_id_position")
        ),
        sa.UniqueConstraint("document_id", name=op.f("uq_cycle_checklist_items_document_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.execute(_BACKFILL)
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("cycle_checklist_items")
