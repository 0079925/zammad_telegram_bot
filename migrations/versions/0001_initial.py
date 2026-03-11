"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_user",
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(256), nullable=False),
        sa.Column("last_name", sa.String(256), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("zammad_user_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("telegram_id"),
    )

    op.create_table(
        "ticket",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("zammad_ticket_id", sa.Integer(), nullable=False),
        sa.Column("zammad_ticket_number", sa.String(32), nullable=False),
        sa.Column(
            "queue_type",
            sa.Enum("support", "manager", name="queuetype"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "new", "open", "pending_reminder", "pending_action", "closed", "merged",
                name="ticketstatus",
            ),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["telegram_id"],
            ["telegram_user.telegram_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ticket_telegram_id", "ticket", ["telegram_id"])
    op.create_index("ix_ticket_zammad_id", "ticket", ["zammad_ticket_id"])

    op.create_table(
        "bot_article",
        sa.Column("article_id", sa.Integer(), nullable=False),
        sa.Column("ticket_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["ticket.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )

    op.create_table(
        "processed_update",
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("update_id"),
    )

    op.create_table(
        "integration_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("zammad_ticket_id", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_integration_log_created_at", "integration_log", ["created_at"]
    )
    op.create_index(
        "ix_integration_log_telegram_id", "integration_log", ["telegram_id"]
    )


def downgrade() -> None:
    op.drop_table("integration_log")
    op.drop_table("processed_update")
    op.drop_table("bot_article")
    op.drop_index("ix_ticket_zammad_id", "ticket")
    op.drop_index("ix_ticket_telegram_id", "ticket")
    op.drop_table("ticket")
    op.drop_table("telegram_user")
    op.execute("DROP TYPE IF EXISTS queuetype")
    op.execute("DROP TYPE IF EXISTS ticketstatus")
