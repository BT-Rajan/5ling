"""assignments, consultant sign-in by emailed link and code (FR-WF-01, FR-WF-02, NFR-SE-02)

Revision ID: 0020
Revises: 0019

assignments: one open assignment per cycle (unique computed column); never deleted; the core of
an assignment never changes and an ended one stays ended (triggers).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_TRIGGERS = {
    "assignments_kept": (
        "CREATE TRIGGER assignments_kept BEFORE DELETE ON assignments FOR EACH ROW "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'assignments are never deleted'"
    ),
    "assignments_fixed": (
        "CREATE TRIGGER assignments_fixed BEFORE UPDATE ON assignments FOR EACH ROW BEGIN "
        "IF NEW.cycle_id <> OLD.cycle_id OR NEW.assignee_id <> OLD.assignee_id"
        " OR NOT (NEW.work_due_date <=> OLD.work_due_date) OR NOT (NEW.note <=> OLD.note)"
        " OR NEW.assigned_by_id <> OLD.assigned_by_id OR NEW.assigned_at <> OLD.assigned_at"
        " OR (OLD.ended_at IS NOT NULL AND (NOT (NEW.ended_at <=> OLD.ended_at)"
        " OR NOT (NEW.ended_by_id <=> OLD.ended_by_id)"
        " OR NOT (NEW.end_reason <=> OLD.end_reason)"
        " OR NOT (NEW.end_note <=> OLD.end_note))) THEN "
        "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'an assignment cannot be changed that way'; "
        "END IF; END"
    ),
}

_ENUMS = (
    # table, column, values before, values after
    (
        "emails",
        "kind",
        "email_kind",
        ("test", "audit_alert", "client_update", "portal_link", "client_alert"),
        ("consultant_sign_in", "assignment"),
    ),
    (
        "notifications",
        "kind",
        "kind",
        ("documents_received", "waiting_on_client", "escalation"),
        ("assigned",),
    ),
    ("document_versions", "uploaded_via", "uploaded_via", ("staff", "portal"), ("consultant",)),
    ("login_challenges", "purpose", "purpose", ("verify", "enrol"), ("email_link",)),
)


def upgrade() -> None:
    for table, column, name, before, added in _ENUMS:
        op.alter_column(
            table,
            column,
            existing_type=mysql.ENUM(*before),
            type_=sa.Enum(*before, *added, name=name),
            existing_nullable=False,
        )
    op.add_column("login_challenges", sa.Column("code_hash", sa.String(length=64), nullable=True))
    op.create_table(
        "assignments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column("assignee_id", sa.BigInteger(), nullable=False),
        sa.Column("work_due_date", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("assigned_by_id", sa.BigInteger(), nullable=False),
        sa.Column("assigned_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("ended_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("ended_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "end_reason",
            sa.Enum("withdrawn", "reassigned", "cycle_closed", name="end_reason"),
            nullable=True,
        ),
        sa.Column("end_note", sa.String(length=1000), nullable=True),
        sa.Column(
            "open_cycle_id",
            sa.BigInteger(),
            sa.Computed("(if(`ended_at` is null,`cycle_id`,NULL))", persisted=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_id"], ["users.id"], name=op.f("fk_assignments_assigned_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["users.id"], name=op.f("fk_assignments_assignee_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_assignments_cycle_id_cycles")
        ),
        sa.ForeignKeyConstraint(
            ["ended_by_id"], ["users.id"], name=op.f("fk_assignments_ended_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignments")),
        sa.UniqueConstraint("open_cycle_id", name=op.f("uq_assignments_open_cycle_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_assignments_assignee_id_ended_at",
        "assignments",
        ["assignee_id", "ended_at"],
        unique=False,
    )
    op.create_index("ix_assignments_cycle_id_id", "assignments", ["cycle_id", "id"], unique=False)
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("assignments")
    op.drop_column("login_challenges", "code_hash")
    for table, column, name, before, added in _ENUMS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Enum(*before, *added, name=name),
            type_=mysql.ENUM(*before),
            existing_nullable=False,
        )
