"""holidays and alert lead-time defaults (FR-PK-03, FR-PK-04)

Revision ID: 0007
Revises: 0006

Defaults: 4 days for monthly and 21 for yearly filings, as the BRD sets. The BRD leaves the
quarterly default to the Administrator; 10 days is only a starting value to change in Setup.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "holidays",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_holidays_created_by_id_users")
        ),
        sa.PrimaryKeyConstraint("day", name=op.f("pk_holidays")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "lead_time_defaults",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("monthly_days", sa.SmallInteger(), nullable=False),
        sa.Column("quarterly_days", sa.SmallInteger(), nullable=False),
        sa.Column("yearly_days", sa.SmallInteger(), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_lead_time_defaults_single_row")),
        sa.CheckConstraint(
            "monthly_days BETWEEN 0 AND 90", name=op.f("ck_lead_time_defaults_monthly_range")
        ),
        sa.CheckConstraint(
            "quarterly_days BETWEEN 0 AND 90", name=op.f("ck_lead_time_defaults_quarterly_range")
        ),
        sa.CheckConstraint(
            "yearly_days BETWEEN 0 AND 90", name=op.f("ck_lead_time_defaults_yearly_range")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], name=op.f("fk_lead_time_defaults_updated_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lead_time_defaults")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.execute(
        "INSERT INTO lead_time_defaults (id, monthly_days, quarterly_days, yearly_days, updated_at)"
        " VALUES (1, 4, 10, 21, UTC_TIMESTAMP(6))"
    )


def downgrade() -> None:
    op.drop_table("lead_time_defaults")
    op.drop_table("holidays")
