"""
models.py — SQLAlchemy ORM models.
"""

from datetime import datetime

from sqlalchemy import Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChatTicketMap(Base):
    """Maps a Telegram chat_id to a Zammad ticket_id.

    One chat can have ONE open ticket at a time.
    When the ticket is closed/resolved, a new one will be created on next message.
    """

    __tablename__ = "chat_ticket_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_chat_id: Mapped[str] = mapped_column(String, index=True, nullable=False)
    zammad_ticket_id: Mapped[int] = mapped_column(Integer, nullable=False)
    zammad_ticket_number: Mapped[str] = mapped_column(String, nullable=True)

    # Telegram user info (cached for Zammad customer lookup)
    telegram_username: Mapped[str] = mapped_column(String, nullable=True)
    telegram_first_name: Mapped[str] = mapped_column(String, nullable=True)
    telegram_last_name: Mapped[str] = mapped_column(String, nullable=True)

    # Zammad customer user id (to avoid repeated lookups)
    zammad_customer_id: Mapped[int] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String, default="open")  # open | closed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
