"""
ORM models.

Naming conventions:
    - telegram_user  — mirrors Telegram user; phone stored here after consent
    - ticket         — maps a Zammad ticket to a Telegram chat + queue type
    - bot_article    — records articles the bot itself created (loop prevention)
    - processed_update — idempotency record for Telegram update IDs
    - integration_log  — append-only audit log for critical events
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db.base import Base


# ── Enums ─────────────────────────────────────────────────────────────────────

class QueueType(str, enum.Enum):
    support = "support"
    manager = "manager"


class TicketStatus(str, enum.Enum):
    new = "new"
    open = "open"
    pending_reminder = "pending_reminder"
    pending_action = "pending_action"
    closed = "closed"
    merged = "merged"


class LogEventType(str, enum.Enum):
    user_created = "user_created"
    phone_saved = "phone_saved"
    zammad_user_linked = "zammad_user_linked"
    ticket_created = "ticket_created"
    article_sent = "article_sent"
    attachment_sent = "attachment_sent"
    agent_reply_forwarded = "agent_reply_forwarded"
    error = "error"


# ── Tables ────────────────────────────────────────────────────────────────────

class TelegramUser(Base):
    """Represents a Telegram user who has interacted with the bot."""

    __tablename__ = "telegram_user"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str] = mapped_column(String(256), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(256))
    # Phone stored only after the user explicitly shares it
    phone: Mapped[str | None] = mapped_column(String(32))
    # Link to the Zammad user account
    zammad_user_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    tickets: Mapped[list["Ticket"]] = relationship(
        "Ticket", back_populates="tg_user", lazy="select"
    )


class Ticket(Base):
    """Maps a Zammad ticket to a Telegram user + queue type."""

    __tablename__ = "ticket"
    __table_args__ = (
        Index("ix_ticket_telegram_id", "telegram_id"),
        Index("ix_ticket_zammad_id", "zammad_ticket_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    telegram_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("telegram_user.telegram_id", ondelete="CASCADE"), nullable=False
    )
    zammad_ticket_id: Mapped[int] = mapped_column(Integer, nullable=False)
    zammad_ticket_number: Mapped[str] = mapped_column(String(32), nullable=False)
    queue_type: Mapped[QueueType] = mapped_column(Enum(QueueType), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus), default=TicketStatus.open, nullable=False
    )
    # Whether this is the currently active ticket for this user+queue
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tg_user: Mapped["TelegramUser"] = relationship("TelegramUser", back_populates="tickets")
    bot_articles: Mapped[list["BotArticle"]] = relationship(
        "BotArticle", back_populates="ticket", lazy="select"
    )


class BotArticle(Base):
    """
    Records every Zammad article that the bot created.
    On incoming webhook events we check this table first —
    if the article_id is present, we skip forwarding to Telegram
    to prevent the feedback loop: Telegram → Zammad → Telegram.
    """

    __tablename__ = "bot_article"

    article_id: Mapped[int] = mapped_column(Integer, primary_key=True)  # Zammad article ID
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("ticket.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    ticket: Mapped["Ticket"] = relationship("Ticket", back_populates="bot_articles")


class ProcessedUpdate(Base):
    """
    Idempotency guard: stores Telegram update_ids that have already been
    processed.  Prevents double-handling on bot restart / duplicate delivery.
    """

    __tablename__ = "processed_update"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class IntegrationLog(Base):
    """
    Append-only audit log for critical integration events.
    Never delete rows; useful for debugging and compliance.
    """

    __tablename__ = "integration_log"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger)
    zammad_ticket_id: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
