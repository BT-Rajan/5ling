"""job runs: background job results, for same-day failure alerts (FR-CY-05)

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("job", sa.String(length=40), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum("scheduled", "manual", "client_created", "package_changed", name="job_trigger"),
            nullable=False,
        ),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("running", "ok", "failed", "skipped", name="job_status"),
            nullable=False,
        ),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("clients_checked", sa.Integer(), nullable=False),
        sa.Column("clients_failed", sa.Integer(), nullable=False),
        sa.Column("cycles_created", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"], name=op.f("fk_job_runs_requested_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_job_runs_job_id", "job_runs", ["job", "id"], unique=False)


def downgrade() -> None:
    op.drop_table("job_runs")
