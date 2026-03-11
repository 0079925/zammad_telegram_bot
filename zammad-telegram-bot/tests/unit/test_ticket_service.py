"""Unit tests for TicketService."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.db.models import QueueType, TicketStatus
from src.services.ticket_service import TicketService, _zammad_state_to_status, _status_display


# ── Status helpers ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state_name,expected", [
    ("open", TicketStatus.open),
    ("new", TicketStatus.new),
    ("closed", TicketStatus.closed),
    ("merged", TicketStatus.merged),
    ("pending reminder", TicketStatus.pending_reminder),
    ("OPEN", TicketStatus.open),  # case-insensitive
    ("unknown_state", TicketStatus.open),  # fallback
])
def test_zammad_state_to_status(state_name, expected):
    assert _zammad_state_to_status(state_name) == expected


def test_status_display_has_label_for_all_statuses():
    for status in TicketStatus:
        label = _status_display(status)
        assert isinstance(label, str)
        assert len(label) > 0


# ── get_or_create_ticket ──────────────────────────────────────────────────────

@pytest.fixture
def service(mock_zammad_client) -> TicketService:
    return TicketService(mock_zammad_client)


@pytest.mark.asyncio
async def test_create_new_ticket_when_none_exists(service, mock_zammad_client, zammad_ticket, db_ticket):
    """Should create a new Zammad ticket when no active DB ticket exists."""
    mock_ticket_repo = MagicMock()
    mock_ticket_repo.get_active = AsyncMock(return_value=None)
    mock_ticket_repo.create = AsyncMock(return_value=db_ticket)

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    with patch("src.services.ticket_service.get_session") as mock_ctx, \
         patch("src.services.ticket_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.ticket_service.IdempotencyRepository", return_value=mock_idem_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        ticket, created = await service.get_or_create_ticket(
            telegram_id=123456789,
            zammad_user_id=42,
            queue=QueueType.support,
            initial_message="Привет",
        )

    assert created is True
    assert ticket.id == zammad_ticket.id
    mock_zammad_client.create_ticket.assert_called_once()


@pytest.mark.asyncio
async def test_reuse_existing_open_ticket(service, mock_zammad_client, zammad_ticket, db_ticket):
    """Should return existing ticket when it is still open."""
    mock_ticket_repo = MagicMock()
    mock_ticket_repo.get_active = AsyncMock(return_value=db_ticket)
    mock_ticket_repo.update_status = AsyncMock(return_value=db_ticket)

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    with patch("src.services.ticket_service.get_session") as mock_ctx, \
         patch("src.services.ticket_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.ticket_service.IdempotencyRepository", return_value=mock_idem_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        ticket, created = await service.get_or_create_ticket(
            telegram_id=123456789,
            zammad_user_id=42,
            queue=QueueType.support,
            initial_message="Ещё вопрос",
        )

    assert created is False
    mock_zammad_client.create_ticket.assert_not_called()


@pytest.mark.asyncio
async def test_create_new_ticket_when_previous_is_closed(
    service, mock_zammad_client, zammad_ticket, db_ticket
):
    """When the existing ticket is closed in Zammad, a new one should be created."""
    db_ticket.status = TicketStatus.open  # DB says open
    closed_zammad = zammad_ticket.model_copy(update={"state": {"name": "closed"}})
    mock_zammad_client.get_ticket = AsyncMock(return_value=closed_zammad)

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.get_active = AsyncMock(return_value=db_ticket)
    mock_ticket_repo.update_status = AsyncMock(return_value=db_ticket)
    mock_ticket_repo.create = AsyncMock(return_value=db_ticket)

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    with patch("src.services.ticket_service.get_session") as mock_ctx, \
         patch("src.services.ticket_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.ticket_service.IdempotencyRepository", return_value=mock_idem_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        ticket, created = await service.get_or_create_ticket(
            telegram_id=123456789,
            zammad_user_id=42,
            queue=QueueType.support,
            initial_message="Снова здравствуйте",
        )

    assert created is True
    mock_zammad_client.create_ticket.assert_called_once()


# ── add_text_article ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_text_article_returns_false_when_no_ticket(service):
    mock_ticket_repo = MagicMock()
    mock_ticket_repo.get_active = AsyncMock(return_value=None)

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    with patch("src.services.ticket_service.get_session") as mock_ctx, \
         patch("src.services.ticket_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.ticket_service.IdempotencyRepository", return_value=mock_idem_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        result = await service.add_text_article(
            telegram_id=123456789,
            queue=QueueType.support,
            text="Сообщение",
        )

    assert result is False


@pytest.mark.asyncio
async def test_add_text_article_sends_to_zammad(service, mock_zammad_client, db_ticket, zammad_article):
    mock_ticket_repo = MagicMock()
    mock_ticket_repo.get_active = AsyncMock(return_value=db_ticket)
    mock_ticket_repo.record_bot_article = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    with patch("src.services.ticket_service.get_session") as mock_ctx, \
         patch("src.services.ticket_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.ticket_service.IdempotencyRepository", return_value=mock_idem_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        result = await service.add_text_article(
            telegram_id=123456789,
            queue=QueueType.support,
            text="Мой вопрос",
        )

    assert result is True
    mock_zammad_client.add_article.assert_called_once_with(
        ticket_id=db_ticket.zammad_ticket_id,
        body="Мой вопрос",
    )
    mock_ticket_repo.record_bot_article.assert_called_once_with(
        zammad_article.id, db_ticket.id
    )
