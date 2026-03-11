"""
Shared pytest fixtures.

Strategy:
    - Unit tests mock the Zammad client and DB session entirely.
    - Integration tests spin up an in-process FastAPI test client.
    - No real external connections are made in tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.models import QueueType, Ticket, TicketStatus, TelegramUser
from src.zammad.schemas import (
    ZammadArticleSchema,
    ZammadTicketSchema,
    ZammadUserSchema,
)


# ── Zammad fixtures ───────────────────────────────────────────────────────────

@pytest.fixture
def zammad_user() -> ZammadUserSchema:
    return ZammadUserSchema(
        id=42,
        login="tg_123456789",
        email="tg_123456789@telegram.bot",
        firstname="Иван",
        lastname="Петров",
        phone="+79001234567",
    )


@pytest.fixture
def zammad_ticket() -> ZammadTicketSchema:
    return ZammadTicketSchema(
        id=100,
        number="100001",
        title="Обращение через Telegram",
        state={"name": "open"},
        group={"name": "Support L1"},
        customer_id=42,
    )


@pytest.fixture
def zammad_article() -> ZammadArticleSchema:
    return ZammadArticleSchema(
        id=200,
        ticket_id=100,
        body="Новое обращение через Telegram.",
        internal=False,
        created_by_id=5,
        content_type="text/plain",
    )


@pytest.fixture
def mock_zammad_client(zammad_user, zammad_ticket, zammad_article) -> MagicMock:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.search_user_by_login = AsyncMock(return_value=None)
    client.search_user_by_phone = AsyncMock(return_value=None)
    client.create_user = AsyncMock(return_value=zammad_user)
    client.update_user = AsyncMock(return_value=zammad_user)
    client.create_ticket = AsyncMock(return_value=zammad_ticket)
    client.get_ticket = AsyncMock(return_value=zammad_ticket)
    client.add_article = AsyncMock(return_value=zammad_article)
    client.add_article_with_attachment = AsyncMock(return_value=zammad_article)
    client.download_attachment = AsyncMock(return_value=b"fake file content")
    return client


# ── DB model helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def db_telegram_user() -> TelegramUser:
    return TelegramUser(
        telegram_id=123456789,
        first_name="Иван",
        last_name="Петров",
        username="ivan_petrov",
        phone="+79001234567",
        zammad_user_id=42,
    )


@pytest.fixture
def db_ticket() -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        telegram_id=123456789,
        zammad_ticket_id=100,
        zammad_ticket_number="100001",
        queue_type=QueueType.support,
        status=TicketStatus.open,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ── Async mock session ────────────────────────────────────────────────────────

@pytest.fixture
def mock_session() -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session
