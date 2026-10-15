"""overdue policy: warn by default; a limit per client (FR-FE-06, BRD Q3)

Revision ID: 0025
Revises: 0024

The Partners answered BRD Q3: warn (do not block), with the limit set per client. clients gains
overdue_limit_paise (NULL: the firm's limit); changes to it are kept in client_changes like other
client changes. The policy moves to "warn" unless an Administrator has already set it.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_FIELDS = ("owner", "package", "status")


def upgrade() -> None:
    op.alter_column(
        "client_changes",
        "field",
        existing_type=mysql.ENUM(*_FIELDS),
        type_=sa.Enum(*_FIELDS, "overdue_limit", name="change_field"),
        existing_nullable=False,
    )
    op.add_column("clients", sa.Column("overdue_limit_paise", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        op.f("ck_clients_limit_not_negative"),
        "clients",
        "overdue_limit_paise IS NULL OR overdue_limit_paise >= 0",
    )
    op.execute("UPDATE fee_policy SET mode = 'warn' WHERE id = 1 AND updated_by_id IS NULL")


def downgrade() -> None:
    op.execute("UPDATE fee_policy SET mode = 'reminder' WHERE id = 1 AND updated_by_id IS NULL")
    op.drop_constraint(op.f("ck_clients_limit_not_negative"), "clients", type_="check")
    op.drop_column("clients", "overdue_limit_paise")
    op.alter_column(
        "client_changes",
        "field",
        existing_type=sa.Enum(*_FIELDS, "overdue_limit", name="change_field"),
        type_=mysql.ENUM(*_FIELDS),
        existing_nullable=False,
    )
