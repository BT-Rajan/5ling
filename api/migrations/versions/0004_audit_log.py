"""audit log: append-only, hash-chained audit_entries and the audit_chain head row

Revision ID: 0004
Revises: 0003

Creating triggers needs the TRIGGER privilege, and with binary logging on (the MySQL 8 default)
either SUPER or log_bin_trust_function_creators=ON. scripts/dev-setup sets the latter.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_TABLE = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}
GENESIS_HASH = "0" * 64

_TRIGGERS = {
    # No row of the trail can be changed or removed, by anyone, through SQL.
    "audit_entries_no_update": (
        "CREATE TRIGGER audit_entries_no_update BEFORE UPDATE ON audit_entries FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_entries is append-only'"
    ),
    "audit_entries_no_delete": (
        "CREATE TRIGGER audit_entries_no_delete BEFORE DELETE ON audit_entries FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_entries is append-only'"
    ),
    # A new row must be the next number and point at the newest hash: no gaps, no forks.
    "audit_entries_extend_chain": """
        CREATE TRIGGER audit_entries_extend_chain BEFORE INSERT ON audit_entries FOR EACH ROW
        BEGIN
            DECLARE head_seq BIGINT DEFAULT NULL;
            DECLARE head_hash VARCHAR(64) DEFAULT NULL;
            SELECT last_seq, last_hash INTO head_seq, head_hash
              FROM audit_chain WHERE id = 1 FOR UPDATE;
            IF head_seq IS NULL OR NEW.seq <> head_seq + 1 OR NEW.prev_hash <> head_hash THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit entry does not extend the chain';
            END IF;
        END
    """,
    # The head moves only here, in the same statement as the insert it records.
    "audit_entries_advance_head": (
        "CREATE TRIGGER audit_entries_advance_head AFTER INSERT ON audit_entries FOR EACH ROW "
        "UPDATE audit_chain SET last_seq = NEW.seq, last_hash = NEW.entry_hash WHERE id = 1"
    ),
    "audit_chain_forward_only": """
        CREATE TRIGGER audit_chain_forward_only BEFORE UPDATE ON audit_chain FOR EACH ROW
        BEGIN
            IF NEW.id <> OLD.id OR NEW.last_seq <> OLD.last_seq + 1 THEN
                SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_chain only moves forward';
            END IF;
        END
    """,
    "audit_chain_no_delete": (
        "CREATE TRIGGER audit_chain_no_delete BEFORE DELETE ON audit_chain FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'audit_chain cannot be deleted'"
    ),
}


def upgrade() -> None:
    op.create_table(
        "audit_entries",
        sa.Column("seq", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("occurred_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.BigInteger(), nullable=True),
        sa.Column("actor_role", sa.String(length=16), nullable=True),
        sa.Column("ip", sa.String(length=45), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("seq", name=op.f("pk_audit_entries")),
        **_TABLE,
    )
    op.create_index("ix_audit_entries_occurred_at", "audit_entries", ["occurred_at"])
    op.create_index(
        "ix_audit_entries_actor_user_id_occurred_at",
        "audit_entries",
        ["actor_user_id", "occurred_at"],
    )
    op.create_index("ix_audit_entries_event_occurred_at", "audit_entries", ["event", "occurred_at"])
    op.create_index(
        "ix_audit_entries_target_type_target_id", "audit_entries", ["target_type", "target_id"]
    )
    op.create_table(
        "audit_chain",
        sa.Column("id", sa.SmallInteger(), autoincrement=False, nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.Column("last_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_audit_chain_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_chain")),
        **_TABLE,
    )
    op.execute(
        sa.text(
            "INSERT INTO audit_chain (id, last_seq, last_hash) VALUES (1, 0, :genesis)"
        ).bindparams(genesis=GENESIS_HASH)
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("audit_chain")
    op.drop_table("audit_entries")
