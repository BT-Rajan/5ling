"""fees: overrides, tax rates, invoice series and invoices (FR-FE-01, FR-FE-02, FR-FE-09)

Revision ID: 0022
Revises: 0021

fee_overrides: one pending per client (unique computed column); never deleted; a decided one
never changes. tax_rates: added, never changed or deleted; GST at 18% from 1 July 2017 is loaded.
invoice_series: the counter only ever goes up by one. invoices: never changed or deleted, and
each must take the number its series counter has just issued, so numbers are gapless.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def _never(table: str, what: str) -> dict[str, str]:
    return {
        f"{table}_kept": (
            f"CREATE TRIGGER {table}_kept BEFORE DELETE ON {table} FOR EACH ROW "
            f"SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{what} are never deleted'"
        )
    }


_TRIGGERS = {
    **_never("fee_overrides", "fee overrides"),
    "fee_overrides_fixed": (
        "CREATE TRIGGER fee_overrides_fixed BEFORE UPDATE ON fee_overrides FOR EACH ROW BEGIN "
        "IF OLD.status <> 'pending' OR NEW.status = 'pending'"
        " OR NEW.client_pk <> OLD.client_pk OR NEW.package_version_id <> OLD.package_version_id"
        " OR NEW.original_paise <> OLD.original_paise OR NEW.amount_paise <> OLD.amount_paise"
        " OR NEW.reason <> OLD.reason OR NEW.requested_by_id <> OLD.requested_by_id"
        " OR NEW.requested_at <> OLD.requested_at THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a fee override can only be decided once'; "
        "END IF; END"
    ),
    **_never("tax_rates", "tax rates"),
    "tax_rates_fixed": (
        "CREATE TRIGGER tax_rates_fixed BEFORE UPDATE ON tax_rates FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'tax rates are never changed'"
    ),
    **_never("invoice_series", "invoice series"),
    "invoice_series_counter": (
        "CREATE TRIGGER invoice_series_counter BEFORE UPDATE ON invoice_series FOR EACH ROW BEGIN "
        "IF NEW.prefix <> OLD.prefix OR NEW.financial_year <> OLD.financial_year"
        " OR NEW.next_seq <> OLD.next_seq + 1 THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'an invoice counter only goes up by one'; "
        "END IF; END"
    ),
    **_never("invoices", "invoices"),
    "invoices_fixed": (
        "CREATE TRIGGER invoices_fixed BEFORE UPDATE ON invoices FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'invoices are never changed'"
    ),
    "invoices_numbered": (
        "CREATE TRIGGER invoices_numbered BEFORE INSERT ON invoices FOR EACH ROW BEGIN "
        "IF NOT (NEW.seq <=> (SELECT next_seq - 1 FROM invoice_series WHERE id = NEW.series_id)) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'an invoice must take the number just issued'; "
        "END IF; END"
    ),
}


def upgrade() -> None:
    op.create_table(
        "invoice_series",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("prefix", sa.String(length=4), nullable=False),
        sa.Column("financial_year", sa.String(length=5), nullable=False),
        sa.Column("next_seq", sa.Integer(), nullable=False),
        sa.CheckConstraint("next_seq >= 1", name=op.f("ck_invoice_series_next_seq_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice_series")),
        sa.UniqueConstraint(
            "prefix", "financial_year", name=op.f("uq_invoice_series_prefix_financial_year")
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "tax_rates",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("rate_bp", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "rate_bp >= 0 AND rate_bp <= 10000", name=op.f("ck_tax_rates_rate_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_tax_rates_created_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tax_rates")),
        sa.UniqueConstraint(
            "code", "effective_from", name=op.f("uq_tax_rates_code_effective_from")
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "fee_overrides",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("package_version_id", sa.BigInteger(), nullable=False),
        sa.Column("original_paise", sa.BigInteger(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "approved", "rejected", name="override_status"),
            nullable=False,
        ),
        sa.Column("requested_by_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("decision_note", sa.String(length=500), nullable=True),
        sa.Column(
            "pending_client_pk",
            sa.BigInteger(),
            sa.Computed("(if(`status` = 'pending',`client_pk`,NULL))", persisted=True),
            nullable=True,
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL) AND (decided_at IS NULL) = (decided_by_id IS NULL)",
            name=op.f("ck_fee_overrides_decided_with_whom"),
        ),
        sa.CheckConstraint("amount_paise > 0", name=op.f("ck_fee_overrides_amount_positive")),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_fee_overrides_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"], ["users.id"], name=op.f("fk_fee_overrides_decided_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["package_versions.id"],
            name=op.f("fk_fee_overrides_package_version_id_package_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"], ["users.id"], name=op.f("fk_fee_overrides_requested_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fee_overrides")),
        sa.UniqueConstraint("pending_client_pk", name=op.f("uq_fee_overrides_pending_client_pk")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_fee_overrides_client_pk_id", "fee_overrides", ["client_pk", "id"], unique=False
    )
    op.create_index("ix_fee_overrides_status", "fee_overrides", ["status"], unique=False)
    op.create_table(
        "invoices",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("invoice_no", sa.String(length=16), nullable=False),
        sa.Column("series_id", sa.BigInteger(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("period_key", sa.String(length=16), nullable=False),
        sa.Column("period_label", sa.String(length=40), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("package_version_id", sa.BigInteger(), nullable=False),
        sa.Column("fee_override_id", sa.BigInteger(), nullable=True),
        sa.Column("tax_rate_id", sa.BigInteger(), nullable=False),
        sa.Column("tax_rate_bp", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("cgst_paise", sa.BigInteger(), nullable=False),
        sa.Column("sgst_paise", sa.BigInteger(), nullable=False),
        sa.Column("igst_paise", sa.BigInteger(), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "amount_paise > 0 AND cgst_paise >= 0 AND sgst_paise >= 0 AND igst_paise >= 0",
            name=op.f("ck_invoices_amounts_valid"),
        ),
        sa.CheckConstraint(
            "igst_paise = 0 OR (cgst_paise = 0 AND sgst_paise = 0)",
            name=op.f("ck_invoices_one_kind"),
        ),
        sa.CheckConstraint("seq >= 1", name=op.f("ck_invoices_seq_positive")),
        sa.CheckConstraint(
            "total_paise = amount_paise + cgst_paise + sgst_paise + igst_paise",
            name=op.f("ck_invoices_total_adds_up"),
        ),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_invoices_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_invoices_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["fee_override_id"],
            ["fee_overrides.id"],
            name=op.f("fk_invoices_fee_override_id_fee_overrides"),
        ),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["package_versions.id"],
            name=op.f("fk_invoices_package_version_id_package_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["series_id"], ["invoice_series.id"], name=op.f("fk_invoices_series_id_invoice_series")
        ),
        sa.ForeignKeyConstraint(
            ["tax_rate_id"], ["tax_rates.id"], name=op.f("fk_invoices_tax_rate_id_tax_rates")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoices")),
        sa.UniqueConstraint(
            "client_pk", "period_key", name=op.f("uq_invoices_client_pk_period_key")
        ),
        sa.UniqueConstraint("invoice_no", name=op.f("uq_invoices_invoice_no")),
        sa.UniqueConstraint("series_id", "seq", name=op.f("uq_invoices_series_id_seq")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_invoices_client_pk_issue_date", "invoices", ["client_pk", "issue_date"], unique=False
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)
    op.execute(
        "INSERT INTO tax_rates (code, rate_bp, effective_from, note, created_at) VALUES "
        "('GST', 1800, '2017-07-01', 'GST on professional services (SAC 9982)', UTC_TIMESTAMP(6))"
    )


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("invoices")
    op.drop_table("fee_overrides")
    op.drop_table("tax_rates")
    op.drop_table("invoice_series")
