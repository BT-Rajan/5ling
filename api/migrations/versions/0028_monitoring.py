"""monitoring: server errors per minute, and alerts to the technical owner (NFR-OB-01)

Revision ID: 0028
Revises: 0027
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "error_minutes",
        sa.Column("minute", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("minute", name=op.f("pk_error_minutes")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "ops_alerts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "job_failed",
                "job_missed",
                "unit_failed",
                "emails_failed",
                "error_spike",
                name="ops_alert_kind",
            ),
            nullable=False,
        ),
        sa.Column("dedupe_key", sa.String(length=120), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=False),
        sa.Column("detail", sa.String(length=1000), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("delivered", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ops_alerts")),
        sa.UniqueConstraint("kind", "dedupe_key", name=op.f("uq_ops_alerts_kind_dedupe_key")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    op.drop_table("ops_alerts")
    op.drop_table("error_minutes")
