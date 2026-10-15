"""fee approvals and overdue policy (FR-FE-06, FR-FE-08)

Revision ID: 0024
Revises: 0023

fee_adjustments: credit notes, discounts and write-offs; never deleted; decided once; a credit
note is numbered only as it is approved. The ledger accepts an adjustment entry only for an
approved adjustment, with the same invoice, kind and amount. fee_policy: one row (reminders only,
until the Partners answer BRD Q3). filing_waivers: one per cycle, never changed or deleted.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "fee_adjustments_kept": (
        "CREATE TRIGGER fee_adjustments_kept BEFORE DELETE ON fee_adjustments FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'fee adjustments are never deleted'"
    ),
    "fee_adjustments_fixed": (
        "CREATE TRIGGER fee_adjustments_fixed BEFORE UPDATE ON fee_adjustments FOR EACH ROW BEGIN "
        "IF OLD.status <> 'pending' OR NEW.status = 'pending'"
        " OR NEW.client_pk <> OLD.client_pk OR NEW.invoice_id <> OLD.invoice_id"
        " OR NEW.kind <> OLD.kind OR NEW.amount_paise <> OLD.amount_paise"
        " OR NEW.reason <> OLD.reason OR NEW.requested_by_id <> OLD.requested_by_id"
        " OR NEW.requested_at <> OLD.requested_at THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a fee adjustment can only be decided once'; "
        "END IF; END"
    ),
    "fee_policy_kept": (
        "CREATE TRIGGER fee_policy_kept BEFORE DELETE ON fee_policy FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'the fee policy is never deleted'"
    ),
    "filing_waivers_kept": (
        "CREATE TRIGGER filing_waivers_kept BEFORE DELETE ON filing_waivers FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'filing waivers are never deleted'"
    ),
    "filing_waivers_fixed": (
        "CREATE TRIGGER filing_waivers_fixed BEFORE UPDATE ON filing_waivers FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'filing waivers are never changed'"
    ),
    "ledger_entries_adjustment": (
        "CREATE TRIGGER ledger_entries_adjustment BEFORE INSERT ON ledger_entries FOR EACH ROW "
        "BEGIN "
        "IF NEW.kind IN ('credit_note', 'discount', 'write_off') AND NOT EXISTS ("
        "SELECT 1 FROM fee_adjustments a WHERE a.id = NEW.adjustment_id AND a.status = 'approved'"
        " AND a.kind = NEW.kind AND a.invoice_id = NEW.invoice_id AND a.client_pk = NEW.client_pk"
        " AND a.amount_paise = NEW.amount_paise) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'an adjustment must be approved first'; "
        "END IF; END"
    ),
}

_ADJUSTMENT_POINTS = (
    "(kind IN ('credit_note', 'discount', 'write_off')) = (adjustment_id IS NOT NULL)"
)


def upgrade() -> None:
    op.create_table(
        "fee_policy",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("mode", sa.Enum("reminder", "warn", "block", name="policy_mode"), nullable=False),
        sa.Column("limit_paise", sa.BigInteger(), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_fee_policy_single_row")),
        sa.CheckConstraint("limit_paise >= 0", name=op.f("ck_fee_policy_limit_not_negative")),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["users.id"], name=op.f("fk_fee_policy_updated_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fee_policy")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "fee_adjustments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("credit_note", "discount", "write_off", name="adjustment_kind"),
            nullable=False,
        ),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="adjustment_status"),
            nullable=False,
        ),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column("credit_note_no", sa.String(length=16), nullable=True),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) AND (decided_at IS NULL) = (decided_by_id IS NULL)",
            name=op.f("ck_fee_adjustments_decided_with_whom"),
        ),
        sa.CheckConstraint(
            "credit_note_no IS NULL OR (kind = 'credit_note' AND status = 'approved')",
            name=op.f("ck_fee_adjustments_numbered_when_approved"),
        ),
        sa.CheckConstraint("amount_paise > 0", name=op.f("ck_fee_adjustments_amount_positive")),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_fee_adjustments_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"], ["users.id"], name=op.f("fk_fee_adjustments_decided_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_fee_adjustments_invoice_id_invoices")
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"], name=op.f("fk_fee_adjustments_requested_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fee_adjustments")),
        sa.UniqueConstraint("credit_note_no", name=op.f("uq_fee_adjustments_credit_note_no")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_fee_adjustments_client_pk_id", "fee_adjustments", ["client_pk", "id"], unique=False
    )
    op.create_index("ix_fee_adjustments_status", "fee_adjustments", ["status"], unique=False)
    op.create_table(
        "filing_waivers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column("granted_by_id", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_filing_waivers_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"], ["users.id"], name=op.f("fk_filing_waivers_granted_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filing_waivers")),
        sa.UniqueConstraint("cycle_id", name=op.f("uq_filing_waivers_cycle_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.add_column("ledger_entries", sa.Column("adjustment_id", sa.BigInteger(), nullable=True))
    op.alter_column(
        "ledger_entries",
        "kind",
        existing_type=mysql.ENUM("payment", "reversal"),
        type_=sa.Enum(
            "payment", "reversal", "credit_note", "discount", "write_off", name="entry_kind"
        ),
        existing_nullable=False,
    )
    op.create_unique_constraint(
        op.f("uq_ledger_entries_adjustment_id"), "ledger_entries", ["adjustment_id"]
    )
    op.create_foreign_key(
        op.f("fk_ledger_entries_adjustment_id_fee_adjustments"),
        "ledger_entries",
        "fee_adjustments",
        ["adjustment_id"],
        ["id"],
    )
    op.create_check_constraint(
        op.f("ck_ledger_entries_adjustment_points"), "ledger_entries", _ADJUSTMENT_POINTS
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)
    op.execute(
        "INSERT INTO fee_policy (id, mode, limit_paise, updated_at)"
        " VALUES (1, 'reminder', 0, UTC_TIMESTAMP(6))"
    )


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_constraint(op.f("ck_ledger_entries_adjustment_points"), "ledger_entries", type_="check")
    op.drop_constraint(
        op.f("fk_ledger_entries_adjustment_id_fee_adjustments"),
        "ledger_entries",
        type_="foreignkey",
    )
    op.drop_constraint(op.f("uq_ledger_entries_adjustment_id"), "ledger_entries", type_="unique")
    op.drop_column("ledger_entries", "adjustment_id")
    op.alter_column(
        "ledger_entries",
        "kind",
        existing_type=sa.Enum(
            "payment", "reversal", "credit_note", "discount", "write_off", name="entry_kind"
        ),
        type_=mysql.ENUM("payment", "reversal"),
        existing_nullable=False,
    )
    op.drop_table("filing_waivers")
    op.drop_table("fee_adjustments")
    op.drop_table("fee_policy")
