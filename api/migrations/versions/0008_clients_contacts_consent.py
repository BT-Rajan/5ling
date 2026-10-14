"""clients, contacts and channel consent; the client ID counter (CR-01)

Revision ID: 0008
Revises: 0007

Client IDs come from client_id_counter's AUTO_INCREMENT, starting at 100001. Triggers refuse a
change to a client's ID, the deletion of a client (close it instead), and any change to issued
IDs or to the consent log.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def _refuse(name: str, timing: str, table: str, message: str, when: str = "TRUE") -> str:
    return (
        f"CREATE TRIGGER {name} BEFORE {timing} ON {table} FOR EACH ROW "
        f"BEGIN IF {when} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}'; END IF; END"
    )


_TRIGGERS = {
    "clients_id_fixed": _refuse(
        "clients_id_fixed",
        "UPDATE",
        "clients",
        "client IDs cannot be changed",
        "NEW.client_id <> OLD.client_id",
    ),
    "clients_kept": _refuse(
        "clients_kept", "DELETE", "clients", "clients are never deleted; close them instead"
    ),
    "client_id_counter_no_update": _refuse(
        "client_id_counter_no_update", "UPDATE", "client_id_counter", "issued client IDs are final"
    ),
    "client_id_counter_no_delete": _refuse(
        "client_id_counter_no_delete", "DELETE", "client_id_counter", "issued client IDs are final"
    ),
    "contact_consents_no_update": _refuse(
        "contact_consents_no_update", "UPDATE", "contact_consents", "the consent log is append-only"
    ),
    "contact_consents_no_delete": _refuse(
        "contact_consents_no_delete", "DELETE", "contact_consents", "the consent log is append-only"
    ),
}


def upgrade() -> None:
    op.create_table(
        "client_id_counter",
        sa.Column("client_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("issued_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("client_id", name=op.f("pk_client_id_counter")),
        mysql_auto_increment="100001",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "clients",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum("individual", "business", name="client_kind"), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("pan", sa.String(length=10), nullable=True),
        sa.Column("gstin", sa.String(length=15), nullable=True),
        sa.Column("owner_id", sa.BigInteger(), nullable=False),
        sa.Column("package_id", sa.BigInteger(), nullable=False),
        sa.Column("package_version_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", sa.Enum("active", "paused", "closed", name="client_status"), nullable=False
        ),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.CheckConstraint(
            "client_id BETWEEN 100001 AND 999999", name=op.f("ck_clients_six_digits")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_clients_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name=op.f("fk_clients_owner_id_users")),
        sa.ForeignKeyConstraint(
            ["package_id"], ["packages.id"], name=op.f("fk_clients_package_id_packages")
        ),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["package_versions.id"],
            name=op.f("fk_clients_package_version_id_package_versions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clients")),
        sa.UniqueConstraint("client_id", name=op.f("uq_clients_client_id")),
        sa.UniqueConstraint("gstin", name=op.f("uq_clients_gstin")),
        sa.UniqueConstraint("pan", name=op.f("uq_clients_pan")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(op.f("ix_clients_legal_name"), "clients", ["legal_name"], unique=False)
    op.create_index("ix_clients_owner_id_status", "clients", ["owner_id", "status"], unique=False)
    op.create_table(
        "client_contacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("phone", sa.String(length=16), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_client_contacts_client_pk_clients")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_client_contacts")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        op.f("ix_client_contacts_client_pk"), "client_contacts", ["client_pk"], unique=False
    )
    op.create_table(
        "contact_consents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("contact_id", sa.BigInteger(), nullable=False),
        sa.Column("channel", sa.Enum("email", "whatsapp", "sms", name="channel"), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("changed_by_id", sa.BigInteger(), nullable=False),
        sa.Column("changed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by_id"], ["users.id"], name=op.f("fk_contact_consents_changed_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["contact_id"],
            ["client_contacts.id"],
            name=op.f("fk_contact_consents_contact_id_client_contacts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_contact_consents")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_contact_consents_contact_id_channel",
        "contact_consents",
        ["contact_id", "channel"],
        unique=False,
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    # Dropping a table drops its indexes; MySQL refuses to drop one that backs a foreign key.
    op.drop_table("contact_consents")
    op.drop_table("client_contacts")
    op.drop_table("clients")
    op.drop_table("client_id_counter")
