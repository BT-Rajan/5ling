"""fee ledger: payments and reversals (FR-FE-03)

Revision ID: 0023
Revises: 0022

ledger_entries: never changed or deleted. A reversal must point at a payment on the same invoice
for the same amount, and a payment can be reversed once (unique key).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "ledger_entries_kept": (
        "CREATE TRIGGER ledger_entries_kept BEFORE DELETE ON ledger_entries FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'ledger entries are never deleted'"
    ),
    "ledger_entries_fixed": (
        "CREATE TRIGGER ledger_entries_fixed BEFORE UPDATE ON ledger_entries FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'ledger entries are never changed'"
    ),
    "ledger_entries_reversal": (
        "CREATE TRIGGER ledger_entries_reversal BEFORE INSERT ON ledger_entries FOR EACH ROW BEGIN "
        "IF NEW.kind = 'reversal' AND NOT EXISTS (SELECT 1 FROM ledger_entries o"
        " WHERE o.id = NEW.reverses_id AND o.kind = 'payment' AND o.invoice_id = NEW.invoice_id"
        " AND o.client_pk = NEW.client_pk AND o.amount_paise = NEW.amount_paise) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a reversal must match the payment it cancels'; "
        "END IF; END"
    ),
}


def upgrade() -> None:
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("invoice_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.Enum("payment", "reversal", name="entry_kind"), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column(
            "mode",
            sa.Enum("bank_transfer", "upi", "cheque", "cash", "card", "other", name="payment_mode"),
            nullable=True,
        ),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("reverses_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "(kind = 'payment') = (mode IS NOT NULL)",
            name=op.f("ck_ledger_entries_payment_has_mode"),
        ),
        sa.CheckConstraint(
            "(kind = 'reversal') = (reverses_id IS NOT NULL)",
            name=op.f("ck_ledger_entries_reversal_points"),
        ),
        sa.CheckConstraint("amount_paise > 0", name=op.f("ck_ledger_entries_amount_positive")),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_ledger_entries_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_ledger_entries_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["invoices.id"], name=op.f("fk_ledger_entries_invoice_id_invoices")
        ),
        sa.ForeignKeyConstraint(
            ["reverses_id"],
            ["ledger_entries.id"],
            name=op.f("fk_ledger_entries_reverses_id_ledger_entries"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        sa.UniqueConstraint("reverses_id", name=op.f("uq_ledger_entries_reverses_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_ledger_entries_client_pk_id", "ledger_entries", ["client_pk", "id"], unique=False
    )
    op.create_index(
        "ix_ledger_entries_invoice_id_id", "ledger_entries", ["invoice_id", "id"], unique=False
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("ledger_entries")
