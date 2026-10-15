"""filing proof: why a filing was late (FR-RP-03)

Revision ID: 0026
Revises: 0025

cycle_filings.late_reason: required by the API when a cycle is filed after its due date, and shown
in the late filings report. Filing proof is never changed (trigger, migration 0021), so the reason
is given with the filing.
"""

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("cycle_filings", sa.Column("late_reason", sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("cycle_filings", "late_reason")
