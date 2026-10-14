"""tickets, filing cycles, stage history and state events (CR-02, CR-03, FR-CY-02)

Revision ID: 0010
Revises: 0009

Ticket numbers come from ticket_counter's AUTO_INCREMENT. Triggers keep issued numbers, ticket
numbers, the stage history and state events from being changed or removed; a state event may
only be marked handled, once.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _refuse(name: str, timing: str, table: str, message: str, when: str = "TRUE") -> str:
    return (
        f"CREATE TRIGGER {name} BEFORE {timing} ON {table} FOR EACH ROW "
        f"BEGIN IF {when} THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '{message}'; END IF; END"
    )


_EVENT_CHANGED = (
    "OLD.handled_at IS NOT NULL OR NEW.handled_at IS NULL OR NEW.kind <> OLD.kind"
    " OR NEW.client_pk <> OLD.client_pk OR NOT (NEW.ticket_id <=> OLD.ticket_id)"
    " OR NEW.from_state <> OLD.from_state OR NEW.to_state <> OLD.to_state"
    " OR NEW.occurred_at <> OLD.occurred_at"
)

_TRIGGERS = {
    name: _refuse(name, timing, table, message, *when)
    for name, timing, table, message, *when in (
        ("ticket_counter_no_update", "UPDATE", "ticket_counter", "issued ticket numbers are final"),
        ("ticket_counter_no_delete", "DELETE", "ticket_counter", "issued ticket numbers are final"),
        (
            "tickets_number_fixed",
            "UPDATE",
            "tickets",
            "ticket numbers cannot be changed",
            "NEW.number <> OLD.number",
        ),
        ("tickets_kept", "DELETE", "tickets", "tickets are never deleted"),
        (
            "stage_changes_no_update",
            "UPDATE",
            "cycle_stage_changes",
            "stage history is append-only",
        ),
        (
            "stage_changes_no_delete",
            "DELETE",
            "cycle_stage_changes",
            "stage history is append-only",
        ),
        (
            "state_events_handled_once",
            "UPDATE",
            "state_events",
            "a state event can only be marked handled, once",
            _EVENT_CHANGED,
        ),
        ("state_events_no_delete", "DELETE", "state_events", "state events are kept"),
    )
}


def upgrade() -> None:
    op.create_table(
        "ticket_counter",
        sa.Column("number", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("issued_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.PrimaryKeyConstraint("number", name=op.f("pk_ticket_counter")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_table(
        "tickets",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("type", sa.Enum("filing", "task", name="ticket_type"), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_tickets_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], name=op.f("fk_tickets_created_by_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tickets")),
        sa.UniqueConstraint("number", name=op.f("uq_tickets_number")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_tickets_client_pk_number", "tickets", ["client_pk", "number"], unique=False)
    op.create_table(
        "cycles",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("filing_id", sa.BigInteger(), nullable=False),
        sa.Column("package_version_id", sa.BigInteger(), nullable=False),
        sa.Column("calendar_version_id", sa.BigInteger(), nullable=False),
        sa.Column("period_key", sa.String(length=8), nullable=False),
        sa.Column("period_label", sa.String(length=40), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("alert_date", sa.Date(), nullable=False),
        sa.Column(
            "stage",
            sa.Enum(
                "scheduled",
                "awaiting_documents",
                "documents_received",
                "assigned",
                "preparing",
                "in_review",
                "ready_to_file",
                "filed",
                "acknowledged",
                "closed",
                name="stage",
            ),
            nullable=False,
        ),
        sa.Column("stage_changed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["calendar_version_id"],
            ["calendar_versions.id"],
            name=op.f("fk_cycles_calendar_version_id_calendar_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_cycles_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"], ["filings.id"], name=op.f("fk_cycles_filing_id_filings")
        ),
        sa.ForeignKeyConstraint(
            ["package_version_id"],
            ["package_versions.id"],
            name=op.f("fk_cycles_package_version_id_package_versions"),
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_cycles_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycles")),
        sa.UniqueConstraint(
            "client_pk",
            "filing_id",
            "period_start",
            name=op.f("uq_cycles_client_pk_filing_id_period_start"),
        ),
        sa.UniqueConstraint("ticket_id", name=op.f("uq_cycles_ticket_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index("ix_cycles_stage_due_date", "cycles", ["stage", "due_date"], unique=False)
    op.create_table(
        "state_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "kind", sa.Enum("cycle_stage", "client_status", name="event_kind"), nullable=False
        ),
        sa.Column("client_pk", sa.BigInteger(), nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=True),
        sa.Column("from_state", sa.String(length=40), nullable=False),
        sa.Column("to_state", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("handled_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["client_pk"], ["clients.id"], name=op.f("fk_state_events_client_pk_clients")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_state_events_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_state_events")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_state_events_handled_at_id", "state_events", ["handled_at", "id"], unique=False
    )
    op.create_table(
        "cycle_stage_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cycle_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "from_stage",
            sa.Enum(
                "scheduled",
                "awaiting_documents",
                "documents_received",
                "assigned",
                "preparing",
                "in_review",
                "ready_to_file",
                "filed",
                "acknowledged",
                "closed",
                name="from_stage",
            ),
            nullable=True,
        ),
        sa.Column(
            "to_stage",
            sa.Enum(
                "scheduled",
                "awaiting_documents",
                "documents_received",
                "assigned",
                "preparing",
                "in_review",
                "ready_to_file",
                "filed",
                "acknowledged",
                "closed",
                name="to_stage",
            ),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("changed_by_id", sa.BigInteger(), nullable=True),
        sa.Column("changed_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["changed_by_id"], ["users.id"], name=op.f("fk_cycle_stage_changes_changed_by_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"], ["cycles.id"], name=op.f("fk_cycle_stage_changes_cycle_id_cycles")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cycle_stage_changes")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_cycle_stage_changes_cycle_id_id",
        "cycle_stage_changes",
        ["cycle_id", "id"],
        unique=False,
    )
    for ddl in _TRIGGERS.values():
        op.execute(ddl)


def downgrade() -> None:
    for name in _TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    op.drop_table("cycle_stage_changes")
    op.drop_table("state_events")
    op.drop_table("cycles")
    op.drop_table("tickets")
    op.drop_table("ticket_counter")
