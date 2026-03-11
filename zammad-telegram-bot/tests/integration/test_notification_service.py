"""
Integration tests for NotificationService — the core anti-loop logic.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.notification_service import NotificationService
from src.zammad.schemas import WebhookArticle, WebhookTicket, ZammadWebhookPayload

INTEGRATION_USER_ID = 5
TELEGRAM_ID = 123456789


def _make_payload(
    *,
    article_id: int = 200,
    internal: bool = False,
    created_by_id: int = 10,
    body: str = "Ответ агента",
    state_name: str = "open",
) -> ZammadWebhookPayload:
    return ZammadWebhookPayload(
        ticket=WebhookTicket(id=100, number="100001", title="Test", state={"name": state_name}),
        article=WebhookArticle(
            id=article_id,
            ticket_id=100,
            body=body,
            internal=internal,
            created_by_id=created_by_id,
            content_type="text/plain",
            attachments=[],
        ),
    )


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_photo = AsyncMock()
    bot.send_document = AsyncMock()
    return bot


@pytest.fixture
def mock_zammad_client():
    client = MagicMock()
    client.download_attachment = AsyncMock(return_value=b"filedata")
    return client


@pytest.fixture
def service(mock_bot, mock_zammad_client):
    return NotificationService(mock_bot, mock_zammad_client)


def _patch_db(ticket_id: int | None = 100, is_bot_article: bool = False):
    """Context manager factory that patches get_session and repos."""
    from contextlib import contextmanager

    mock_db_ticket = MagicMock()
    mock_db_ticket.telegram_id = TELEGRAM_ID
    mock_db_ticket.status.value = "open"

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.is_bot_article = AsyncMock(return_value=is_bot_article)
    mock_ticket_repo.get_by_zammad_id = AsyncMock(
        return_value=mock_db_ticket if ticket_id else None
    )
    mock_ticket_repo.update_status = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    return (
        patch("src.services.notification_service.get_session", return_value=session_mock),
        patch("src.services.notification_service.TicketRepository", return_value=mock_ticket_repo),
        patch("src.services.notification_service.IdempotencyRepository", return_value=mock_idem_repo),
        mock_bot_placeholder := mock_db_ticket,
    )


# ── Internal note must NOT be forwarded ──────────────────────────────────────

@pytest.mark.asyncio
async def test_internal_article_not_forwarded(service, mock_bot):
    payload = _make_payload(internal=True)

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.notification_service.get_session", return_value=session_mock):
        await service.handle_webhook(payload)

    mock_bot.send_message.assert_not_called()


# ── Bot's own articles must NOT be forwarded (by user ID) ─────────────────────

@pytest.mark.asyncio
async def test_bot_own_article_not_forwarded_by_user_id(service, mock_bot):
    """Article created by integration user → skip."""
    payload = _make_payload(created_by_id=INTEGRATION_USER_ID)

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.notification_service.get_session", return_value=session_mock), \
         patch("src.services.notification_service.get_settings") as mock_cfg:
        cfg = MagicMock()
        cfg.zammad_integration_user_id = INTEGRATION_USER_ID
        mock_cfg.return_value = cfg
        await service.handle_webhook(payload)

    mock_bot.send_message.assert_not_called()


# ── Bot's own articles must NOT be forwarded (by DB record) ──────────────────

@pytest.mark.asyncio
async def test_bot_own_article_not_forwarded_by_db(service, mock_bot):
    """Article ID is in bot_article table → skip."""
    payload = _make_payload(article_id=999, created_by_id=77)

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.is_bot_article = AsyncMock(return_value=True)  # ← in DB
    mock_ticket_repo.get_by_zammad_id = AsyncMock()
    mock_ticket_repo.update_status = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.notification_service.get_session", return_value=session_mock), \
         patch("src.services.notification_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.notification_service.get_settings") as mock_cfg:
        cfg = MagicMock()
        cfg.zammad_integration_user_id = INTEGRATION_USER_ID
        mock_cfg.return_value = cfg
        await service.handle_webhook(payload)

    mock_bot.send_message.assert_not_called()


# ── Agent reply SHOULD be forwarded ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_reply_forwarded(service, mock_bot):
    payload = _make_payload(body="Добрый день, чем могу помочь?", created_by_id=99)

    mock_db_ticket = MagicMock()
    mock_db_ticket.telegram_id = TELEGRAM_ID

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.is_bot_article = AsyncMock(return_value=False)
    mock_ticket_repo.get_by_zammad_id = AsyncMock(return_value=mock_db_ticket)
    mock_ticket_repo.update_status = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.notification_service.get_session", return_value=session_mock), \
         patch("src.services.notification_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.notification_service.IdempotencyRepository", return_value=mock_idem_repo), \
         patch("src.services.notification_service.get_settings") as mock_cfg:
        cfg = MagicMock()
        cfg.zammad_integration_user_id = INTEGRATION_USER_ID
        mock_cfg.return_value = cfg
        await service.handle_webhook(payload)

    mock_bot.send_message.assert_called_once()
    call_kwargs = mock_bot.send_message.call_args
    assert call_kwargs.kwargs["chat_id"] == TELEGRAM_ID
    assert "Добрый день" in call_kwargs.kwargs["text"]


# ── Ticket closed notification ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_closed_ticket_sends_notification(service, mock_bot):
    payload = _make_payload(body="Вопрос решён.", state_name="closed", created_by_id=99)

    mock_db_ticket = MagicMock()
    mock_db_ticket.telegram_id = TELEGRAM_ID

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.is_bot_article = AsyncMock(return_value=False)
    mock_ticket_repo.get_by_zammad_id = AsyncMock(return_value=mock_db_ticket)
    mock_ticket_repo.update_status = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.notification_service.get_session", return_value=session_mock), \
         patch("src.services.notification_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.notification_service.IdempotencyRepository", return_value=mock_idem_repo), \
         patch("src.services.notification_service.get_settings") as mock_cfg:
        cfg = MagicMock()
        cfg.zammad_integration_user_id = INTEGRATION_USER_ID
        mock_cfg.return_value = cfg
        await service.handle_webhook(payload)

    # Should have been called twice: once for body, once for close notification
    assert mock_bot.send_message.call_count == 2
    last_call = mock_bot.send_message.call_args_list[-1]
    assert "закрыт" in last_call.kwargs["text"]
