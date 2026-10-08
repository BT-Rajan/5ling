"""baseline: app_meta

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_meta",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("value", sa.String(255), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("key", name="pk_app_meta"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.execute(
        "INSERT INTO app_meta (`key`, `value`, updated_at) VALUES ('baseline', '0001', UTC_TIMESTAMP(6))"
    )


def downgrade() -> None:
    op.drop_table("app_meta")
