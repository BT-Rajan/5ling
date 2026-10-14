"""catalog: filing types with versioned calendars, versioned packages and checklists

Revision ID: 0006
Revises: 0005

Published versions are frozen by triggers (FR-PK-05): neither the version row nor the filings and
checklist items under it can be changed or removed once published_at is set.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


_TRIGGERS = {
    "calendar_versions_frozen_upd": (
        "CREATE TRIGGER calendar_versions_frozen_upd BEFORE UPDATE ON calendar_versions "
        "FOR EACH ROW BEGIN IF OLD.published_at IS NOT NULL THEN SIGNAL SQLSTATE '45000' "
        "SET MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "calendar_versions_frozen_del": (
        "CREATE TRIGGER calendar_versions_frozen_del BEFORE DELETE ON calendar_versions "
        "FOR EACH ROW BEGIN IF OLD.published_at IS NOT NULL THEN SIGNAL SQLSTATE '45000' "
        "SET MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "package_versions_frozen_upd": (
        "CREATE TRIGGER package_versions_frozen_upd BEFORE UPDATE ON package_versions FOR "
        "EACH ROW BEGIN IF OLD.published_at IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET "
        "MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "package_versions_frozen_del": (
        "CREATE TRIGGER package_versions_frozen_del BEFORE DELETE ON package_versions FOR "
        "EACH ROW BEGIN IF OLD.published_at IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET "
        "MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "package_filings_frozen_ins": (
        "CREATE TRIGGER package_filings_frozen_ins BEFORE INSERT ON package_filings FOR "
        "EACH ROW BEGIN IF (SELECT published_at FROM package_versions WHERE id = "
        "NEW.package_version_id) IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET "
        "MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "package_filings_frozen_upd": (
        "CREATE TRIGGER package_filings_frozen_upd BEFORE UPDATE ON package_filings FOR "
        "EACH ROW BEGIN IF (SELECT published_at FROM package_versions WHERE id = "
        "OLD.package_version_id) IS NOT NULL OR (SELECT published_at FROM "
        "package_versions WHERE id = NEW.package_version_id) IS NOT NULL THEN SIGNAL "
        "SQLSTATE '45000' SET MESSAGE_TEXT = 'published versions cannot be changed'; END "
        "IF; END"
    ),
    "package_filings_frozen_del": (
        "CREATE TRIGGER package_filings_frozen_del BEFORE DELETE ON package_filings FOR "
        "EACH ROW BEGIN IF (SELECT published_at FROM package_versions WHERE id = "
        "OLD.package_version_id) IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET "
        "MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "checklist_items_frozen_ins": (
        "CREATE TRIGGER checklist_items_frozen_ins BEFORE INSERT ON "
        "package_checklist_items FOR EACH ROW BEGIN IF (SELECT v.published_at FROM "
        "package_filings f JOIN package_versions v ON v.id = f.package_version_id WHERE "
        "f.id = NEW.package_filing_id) IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET "
        "MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "checklist_items_frozen_upd": (
        "CREATE TRIGGER checklist_items_frozen_upd BEFORE UPDATE ON "
        "package_checklist_items FOR EACH ROW BEGIN IF (SELECT v.published_at FROM "
        "package_filings f JOIN package_versions v ON v.id = f.package_version_id WHERE "
        "f.id = OLD.package_filing_id) IS NOT NULL OR (SELECT v.published_at FROM "
        "package_filings f JOIN package_versions v ON v.id = f.package_version_id WHERE "
        "f.id = NEW.package_filing_id) IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET "
        "MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
    "checklist_items_frozen_del": (
        "CREATE TRIGGER checklist_items_frozen_del BEFORE DELETE ON "
        "package_checklist_items FOR EACH ROW BEGIN IF (SELECT v.published_at FROM "
        "package_filings f JOIN package_versions v ON v.id = f.package_version_id WHERE "
        "f.id = OLD.package_filing_id) IS NOT NULL THEN SIGNAL SQLSTATE '45000' SET "
        "MESSAGE_TEXT = 'published versions cannot be changed'; END IF; END"
    ),
}


def upgrade() -> None:
    op.create_table(
        "filings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("default_checklist", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_filings_created_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_filings")),
        sa.UniqueConstraint("code", name=op.f("uq_filings_code")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "packages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_packages_created_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packages")),
        sa.UniqueConstraint("name", name=op.f("uq_packages_name")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "calendar_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "frequency", sa.Enum("monthly", "quarterly", "yearly", name="frequency"), nullable=False
        ),
        sa.Column(
            "period_basis",
            sa.Enum("financial_year", "calendar_year", name="period_basis"),
            nullable=False,
        ),
        sa.Column("due_months_after", sa.SmallInteger(), nullable=False),
        sa.Column("due_day", sa.SmallInteger(), nullable=False),
        sa.Column("due_overrides", sa.JSON(), nullable=False),
        sa.Column("lead_days", sa.SmallInteger(), nullable=True),
        sa.Column(
            "holiday_rule",
            sa.Enum("earlier_working_day", "as_is", name="holiday_rule"),
            nullable=False,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("published_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(published_at IS NULL) = (published_by_id IS NULL)",
            name=op.f("ck_calendar_versions_published_by_whom"),
        ),
        sa.CheckConstraint("due_day BETWEEN 1 AND 31", name=op.f("ck_calendar_versions_day_range")),
        sa.CheckConstraint(
            "due_months_after BETWEEN 0 AND 12",
            name=op.f("ck_calendar_versions_months_after_range"),
        ),
        sa.CheckConstraint(
            "lead_days IS NULL OR lead_days BETWEEN 0 AND 90",
            name=op.f("ck_calendar_versions_lead_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_calendar_versions_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"], ["filings.id"], name=op.f("fk_calendar_versions_filing_id_filings")
        ),
        sa.ForeignKeyConstraint(
            ["published_by_id"],
            ["users.id"],
            name=op.f("fk_calendar_versions_published_by_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_versions")),
        sa.UniqueConstraint(
            "filing_id", "version", name=op.f("uq_calendar_versions_filing_id_version")
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "package_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fee_paise", sa.BigInteger(), nullable=True),
        sa.Column(
            "billing_frequency",
            sa.Enum("monthly", "quarterly", "yearly", "one_time", name="billing_frequency"),
            nullable=True,
        ),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("published_by_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            "(published_at IS NULL) = (published_by_id IS NULL)",
            name=op.f("ck_package_versions_published_by_whom"),
        ),
        sa.CheckConstraint(
            "fee_paise IS NULL OR fee_paise > 0", name=op.f("ck_package_versions_fee_positive")
        ),
        sa.CheckConstraint(
            "published_at IS NULL OR (fee_paise IS NOT NULL AND billing_frequency IS NOT NULL)",
            name=op.f("ck_package_versions_published_has_fee"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_package_versions_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["package_id"], ["packages.id"], name=op.f("fk_package_versions_package_id_packages")
        ),
        sa.ForeignKeyConstraint(
            ["published_by_id"],
            ["users.id"],
            name=op.f("fk_package_versions_published_by_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_versions")),
        sa.UniqueConstraint(
            "package_id", "version", name=op.f("uq_package_versions_package_id_version")
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "package_filings",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("package_version_id", sa.BigInteger(), nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["filing_id"], ["filings.id"], name=op.f("fk_package_filings_filing_id_filings")
        ),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["package_versions.id"],
            name=op.f("fk_package_filings_package_version_id_package_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_filings")),
        sa.UniqueConstraint(
            "package_version_id",
            "filing_id",
            name=op.f("uq_package_filings_package_version_id_filing_id"),
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "package_checklist_items",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("package_filing_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_filing_id"],
            ["package_filings.id"],
            name=op.f("fk_package_checklist_items_package_filing_id_package_filings"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_checklist_items")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        op.f("ix_package_checklist_items_package_filing_id"),
        "package_checklist_items",
        ["package_filing_id"],
        unique=False,
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("package_checklist_items")
    op.drop_table("package_filings")
    op.drop_table("package_versions")
    op.drop_table("calendar_versions")
    op.drop_table("packages")
    op.drop_table("filings")
