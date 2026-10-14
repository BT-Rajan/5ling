"""tasks: the details of TASK tickets (CR-02); task status changes are state events (CR-03)

Revision ID: 0012
Revises: 0011

Downgrading fails while any task_status state event exists; that is deliberate (events are kept).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticket_id", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("assignee_id", sa.BigInteger(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column(
            "status", sa.Enum("open", "done", "cancelled", name="task_status"), nullable=False
        ),
        sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["users.id"], name=op.f("fk_tasks_assignee_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"], ["tickets.id"], name=op.f("fk_tasks_ticket_id_tickets")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tasks")),
        sa.UniqueConstraint("ticket_id", name=op.f("uq_tasks_ticket_id")),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_tasks_assignee_id_status_due_date",
        "tasks",
        ["assignee_id", "status", "due_date"],
        unique=False,
    )
    op.alter_column(
        "state_events",
        "kind",
        existing_type=mysql.ENUM("cycle_stage", "client_status"),
        type_=sa.Enum("cycle_stage", "client_status", "task_status", name="event_kind"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "state_events",
        "kind",
        existing_type=sa.Enum("cycle_stage", "client_status", "task_status", name="event_kind"),
        type_=mysql.ENUM("cycle_stage", "client_status"),
        existing_nullable=False,
    )
    op.drop_table("tasks")
