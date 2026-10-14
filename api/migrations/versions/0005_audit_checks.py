"""audit checks: results of each chain verification, append-only

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

_TRIGGERS = {
    "audit_checks_no_update": (
        "CREATE TRIGGER audit_checks_no_update BEFORE UPDATE ON audit_checks FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_checks is append-only'"
    ),
    "audit_checks_no_delete": (
        "CREATE TRIGGER audit_checks_no_delete BEFORE DELETE ON audit_checks FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_checks is append-only'"
    ),
}


def upgrade() -> None:
    op.create_table(
        "audit_checks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column(
            "trigger", sa.Enum("scheduled", "manual", "export", name="trigger"), nullable=False
        ),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("entries_checked", sa.BigInteger(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
        sa.Column("first_bad_seq", sa.BigInteger(), nullable=True),
        sa.Column("problem", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_checks")),
        **_TABLE,
    )
    op.create_index("ix_audit_checks_ok_id", "audit_checks", ["ok", "id"])
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("audit_checks")
