"""documents and their versions (FR-AL-04, FR-AL-05)

Revision ID: 0016
Revises: 0015

Triggers: documents and versions are never deleted; a version's file, size, hash, name and
uploader never change; its scan result only moves from pending to clean or infected, once; a
document's version count only goes up.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "documents_kept": (
        "CREATE TRIGGER documents_kept BEFORE DELETE ON documents FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'documents are never deleted'"
    ),
    "documents_fixed": (
        "CREATE TRIGGER documents_fixed BEFORE UPDATE ON documents FOR EACH ROW BEGIN "
        "IF NEW.client_pk <> OLD.client_pk OR NEW.cycle_id <> OLD.cycle_id"
        " OR NEW.created_at <> OLD.created_at OR NOT (NEW.created_by_id <=> OLD.created_by_id)"
        " OR NEW.latest_version < OLD.latest_version THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a document cannot be changed that way'; "
        "END IF; END"
    ),
    "document_versions_kept": (
        "CREATE TRIGGER document_versions_kept BEFORE DELETE ON document_versions FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'versions are never deleted'"
    ),
    "document_versions_fixed": (
        "CREATE TRIGGER document_versions_fixed BEFORE UPDATE ON document_versions FOR EACH ROW "
        "BEGIN "
        "IF NEW.document_id <> OLD.document_id OR NEW.version <> OLD.version"
        " OR NEW.storage_key <> OLD.storage_key OR NEW.filename <> OLD.filename"
        " OR NEW.content_type <> OLD.content_type OR NEW.size_bytes <> OLD.size_bytes"
        " OR NEW.sha256 <> OLD.sha256 OR NEW.uploaded_via <> OLD.uploaded_via"
        " OR NOT (NEW.uploaded_by_id <=> OLD.uploaded_by_id)"
        " OR NOT (NEW.portal_link_id <=> OLD.portal_link_id)"
        " OR NEW.uploaded_at <> OLD.uploaded_at"
        " OR (NEW.scan_status <> OLD.scan_status AND OLD.scan_status <> 'pending') THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'a stored version cannot be changed'; "
        "END IF; END"
    ),
}

_TABLE = {
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
    "mysql_engine": "InnoDB",
}


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("latest_version", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_documents_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_documents_created_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_documents_cycle_id_cycles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        **_TABLE,
    )
    op.create_index("ix_documents_cycle_id_id", "documents", ["cycle_id", "id"], unique=False)
    op.create_table(
        "document_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=150), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_via", sa.Enum("staff", "portal", name="uploaded_via"), nullable=False),
        sa.Column("uploaded_by_id", sa.BigInteger(), nullable=True),
        sa.Column("portal_link_id", sa.BigInteger(), nullable=True),
        sa.Column("uploaded_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column(
            "scan_status",
            sa.Enum("pending", "clean", "infected", name="scan_status"),
            nullable=False,
        ),
        sa.Column("scanned_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("scan_detail", sa.String(length=150), nullable=True),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_versions_document_id_documents"),
        ),
        sa.ForeignKeyConstraint(
            ["portal_link_id"],
            ["portal_links.id"],
            name=op.f("fk_document_versions_portal_link_id_portal_links"),
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name=op.f("fk_document_versions_uploaded_by_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint(
            "document_id", "version", name=op.f("uq_document_versions_document_id_version")
        ),
        sa.UniqueConstraint("storage_key", name=op.f("uq_document_versions_storage_key")),
        **_TABLE,
    )
    op.create_index(
        "ix_document_versions_scan_status_id",
        "document_versions",
        ["scan_status", "id"],
        unique=False,
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("document_versions")
    op.drop_table("documents")
