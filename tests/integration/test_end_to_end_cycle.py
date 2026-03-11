"""
End-to-end cycle test.

Verifies the complete business flow without real external connections:

  1.  New user registers (no phone yet)
  2.  User shares phone → Zammad user created automatically
  3.  User selects "Support" queue
  4.  Ticket created in Zammad, user sees confirmation with ticket number
  5.  User sends a text message → article added to existing ticket
  6.  User sends an attachment → attachment article added
  7.  Agent replies in Zammad → webhook fires → message delivered to Telegram
  8.  Agent sends a reply with attachment → file forwarded to Telegram
  9.  Internal note arrives → NOT forwarded to Telegram
  10. Bot's own article arrives via webhook → NOT forwarded (anti-loop)
  11. Ticket is closed in Zammad → Telegram user notified
  12. User sends a new message after closure → NEW ticket created

All Zammad API calls and DB sessions are mocked.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from src.db.models import QueueType, Ticket, TicketStatus, TelegramUser
from src.services.notification_service import NotificationService
from src.services.ticket_service import TicketService
from src.services.user_service import UserService
from src.zammad.schemas import (
    WebhookArticle,
    WebhookTicket,
    ZammadArticleSchema,
    ZammadAttachmentSchema,
    ZammadTicketSchema,
    ZammadUserSchema,
    ZammadWebhookPayload,
)

# ── Constants ─────────────────────────────────────────────────────────────────

TG_ID = 111222333
TG_PHONE = "+79991234567"
TG_FIRST = "Алексей"
INTEGRATION_USER_ID = 5
ZAMMAD_USER_ID = 77
ZAMMAD_TICKET_ID = 200
ZAMMAD_TICKET_NUMBER = "200001"
ZAMMAD_ARTICLE_ID = 300


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db_ticket(
    status: TicketStatus = TicketStatus.open,
    is_active: bool = True,
) -> Ticket:
    return Ticket(
        id=uuid.uuid4(),
        telegram_id=TG_ID,
        zammad_ticket_id=ZAMMAD_TICKET_ID,
        zammad_ticket_number=ZAMMAD_TICKET_NUMBER,
        queue_type=QueueType.support,
        status=status,
        is_active=is_active,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def _make_zammad_ticket(state_name: str = "open") -> ZammadTicketSchema:
    return ZammadTicketSchema(
        id=ZAMMAD_TICKET_ID,
        number=ZAMMAD_TICKET_NUMBER,
        title="Test ticket",
        state={"name": state_name},
        group={"name": "Support L1"},
        customer_id=ZAMMAD_USER_ID,
    )


def _make_zammad_user() -> ZammadUserSchema:
    return ZammadUserSchema(
        id=ZAMMAD_USER_ID,
        login=f"tg_{TG_ID}",
        email=f"tg_{TG_ID}@telegram.bot",
        firstname=TG_FIRST,
        phone=TG_PHONE,
    )


def _make_zammad_article(
    article_id: int = ZAMMAD_ARTICLE_ID,
    internal: bool = False,
    created_by_id: int = 99,
    body: str = "Ответ агента",
    attachments: list | None = None,
) -> ZammadArticleSchema:
    return ZammadArticleSchema(
        id=article_id,
        ticket_id=ZAMMAD_TICKET_ID,
        body=body,
        internal=internal,
        created_by_id=created_by_id,
        content_type="text/plain",
        attachments=attachments or [],
    )


def _patch_session(
    user: TelegramUser | None = None,
    ticket: Ticket | None = None,
    is_bot_article: bool = False,
):
    """
    Returns a context manager that patches get_session and all repositories
    with consistent mock objects.
    """
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    mock_user_repo = MagicMock()
    mock_user_repo.get_by_telegram_id = AsyncMock(return_value=user)
    mock_user_repo.upsert = AsyncMock(return_value=user)
    mock_user_repo.save_phone = AsyncMock()
    mock_user_repo.link_zammad_user = AsyncMock()

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.get_active = AsyncMock(return_value=ticket)
    mock_ticket_repo.get_by_zammad_id = AsyncMock(return_value=ticket)
    mock_ticket_repo.create = AsyncMock(return_value=ticket)
    mock_ticket_repo.update_status = AsyncMock(return_value=ticket)
    mock_ticket_repo.is_bot_article = AsyncMock(return_value=is_bot_article)
    mock_ticket_repo.record_bot_article = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.is_processed = AsyncMock(return_value=False)
    mock_idem_repo.mark_processed = AsyncMock()
    mock_idem_repo.write_log = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.add = MagicMock()
    session_mock.commit = AsyncMock()
    session_mock.rollback = AsyncMock()

    return (
        patch("src.services.user_service.get_session", return_value=session_mock),
        patch("src.services.user_service.UserRepository", return_value=mock_user_repo),
        patch("src.services.user_service.IdempotencyRepository", return_value=mock_idem_repo),
        patch("src.services.ticket_service.get_session", return_value=session_mock),
        patch("src.services.ticket_service.TicketRepository", return_value=mock_ticket_repo),
        patch("src.services.ticket_service.IdempotencyRepository", return_value=mock_idem_repo),
        patch("src.services.notification_service.get_session", return_value=session_mock),
        patch("src.services.notification_service.TicketRepository", return_value=mock_ticket_repo),
        patch("src.services.notification_service.IdempotencyRepository", return_value=mock_idem_repo),
        patch("src.services.notification_service.get_settings", return_value=MagicMock(
            zammad_integration_user_id=INTEGRATION_USER_ID
        )),
        mock_user_repo,
        mock_ticket_repo,
        mock_idem_repo,
    )


# ── Step 1-2: User registration + phone ──────────────────────────────────────

@pytest.mark.asyncio
async def test_step1_ensure_user_no_phone():
    """Fresh user has no phone; has_phone returns False."""
    mock_zammad = MagicMock()
    service = UserService(mock_zammad)

    db_user_no_phone = TelegramUser(
        telegram_id=TG_ID,
        first_name=TG_FIRST,
        phone=None,
        zammad_user_id=None,
    )
    mock_user_repo = MagicMock()
    mock_user_repo.upsert = AsyncMock(return_value=db_user_no_phone)
    mock_user_repo.get_by_telegram_id = AsyncMock(return_value=db_user_no_phone)

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.user_service.get_session", return_value=session_mock), \
         patch("src.services.user_service.UserRepository", return_value=mock_user_repo), \
         patch("src.services.user_service.IdempotencyRepository", return_value=mock_idem_repo):
        await service.ensure_user(telegram_id=TG_ID, first_name=TG_FIRST)
        has = await service.has_phone(TG_ID)

    assert has is False


@pytest.mark.asyncio
async def test_step2_register_phone_creates_zammad_user():
    """
    When user shares phone for the first time:
    - Phone saved to DB
    - Zammad user created automatically
    - Zammad user ID linked to TelegramUser
    """
    zammad_user = _make_zammad_user()
    mock_zammad = MagicMock()
    mock_zammad.search_user_by_login = AsyncMock(return_value=None)
    mock_zammad.search_user_by_phone = AsyncMock(return_value=None)
    mock_zammad.create_user = AsyncMock(return_value=zammad_user)

    service = UserService(mock_zammad)

    mock_user_repo = MagicMock()
    mock_user_repo.save_phone = AsyncMock()
    mock_user_repo.link_zammad_user = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.user_service.get_session", return_value=session_mock), \
         patch("src.services.user_service.UserRepository", return_value=mock_user_repo), \
         patch("src.services.user_service.IdempotencyRepository", return_value=mock_idem_repo):
        zammad_id = await service.register_phone(
            telegram_id=TG_ID,
            phone=TG_PHONE,
            first_name=TG_FIRST,
        )

    # Zammad user was created automatically
    mock_zammad.create_user.assert_called_once()
    call_kwargs = mock_zammad.create_user.call_args.kwargs
    assert call_kwargs["login"] == f"tg_{TG_ID}"
    assert call_kwargs["phone"] == TG_PHONE

    # Linked in our DB
    mock_user_repo.link_zammad_user.assert_called_once_with(TG_ID, ZAMMAD_USER_ID)
    assert zammad_id == ZAMMAD_USER_ID


# ── Step 3-4: Queue selection + ticket creation ───────────────────────────────

@pytest.mark.asyncio
async def test_step3_create_ticket_on_queue_select():
    """
    No existing active ticket → new ticket created in Zammad.
    User sees ticket number and status.
    """
    zammad_ticket = _make_zammad_ticket()
    db_ticket = _make_db_ticket()

    mock_zammad = MagicMock()
    mock_zammad.create_ticket = AsyncMock(return_value=zammad_ticket)
    mock_zammad.get_ticket = AsyncMock(return_value=zammad_ticket)

    service = TicketService(mock_zammad)

    patches = _patch_session(ticket=None)
    *ctx_managers, mock_user_repo, mock_ticket_repo, mock_idem_repo = patches
    mock_ticket_repo.get_active = AsyncMock(return_value=None)
    mock_ticket_repo.create = AsyncMock(return_value=db_ticket)

    with (
        ctx_managers[0], ctx_managers[1], ctx_managers[2],
        ctx_managers[3], ctx_managers[4], ctx_managers[5],
    ):
        ticket, created = await service.get_or_create_ticket(
            telegram_id=TG_ID,
            zammad_user_id=ZAMMAD_USER_ID,
            queue=QueueType.support,
            initial_message="Здравствуйте, у меня вопрос",
        )

    assert created is True
    assert ticket.number == ZAMMAD_TICKET_NUMBER
    mock_zammad.create_ticket.assert_called_once()
    # Ticket must be created in the correct Zammad group
    create_kwargs = mock_zammad.create_ticket.call_args.kwargs
    assert "Support" in create_kwargs["group"]


# ── Step 5: User sends text → article added to EXISTING ticket ────────────────

@pytest.mark.asyncio
async def test_step5_text_goes_to_existing_ticket():
    """
    Second user message must be added to the SAME ticket, not a new one.
    """
    db_ticket = _make_db_ticket()
    article = _make_zammad_article()

    mock_zammad = MagicMock()
    mock_zammad.add_article = AsyncMock(return_value=article)

    service = TicketService(mock_zammad)
    patches = _patch_session(ticket=db_ticket)
    *ctx_managers, mock_user_repo, mock_ticket_repo, mock_idem_repo = patches

    with (
        ctx_managers[3], ctx_managers[4], ctx_managers[5],
    ):
        sent = await service.add_text_article(
            telegram_id=TG_ID,
            queue=QueueType.support,
            text="Уточняющий вопрос",
        )

    assert sent is True
    # Article added to the existing ticket, not a new one created
    mock_zammad.add_article.assert_called_once_with(
        ticket_id=ZAMMAD_TICKET_ID,
        body="Уточняющий вопрос",
    )
    mock_zammad.create_ticket.assert_not_called() if hasattr(mock_zammad, 'create_ticket') else None
    # Article ID recorded to prevent loop
    mock_ticket_repo.record_bot_article.assert_called_once_with(article.id, db_ticket.id)


# ── Step 6: User sends attachment ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_step6_attachment_uploaded_to_zammad():
    """Attachment from Telegram is uploaded to Zammad as base64 article."""
    db_ticket = _make_db_ticket()
    article = _make_zammad_article()

    mock_zammad = MagicMock()
    mock_zammad.add_article_with_attachment = AsyncMock(return_value=article)

    service = TicketService(mock_zammad)
    patches = _patch_session(ticket=db_ticket)
    *ctx_managers, mock_user_repo, mock_ticket_repo, mock_idem_repo = patches

    with (
        ctx_managers[3], ctx_managers[4], ctx_managers[5],
    ):
        sent = await service.add_attachment_article(
            telegram_id=TG_ID,
            queue=QueueType.support,
            caption="Скриншот ошибки",
            filename="screenshot.jpg",
            content=b"fake jpeg bytes",
            content_type="image/jpeg",
        )

    assert sent is True
    mock_zammad.add_article_with_attachment.assert_called_once()
    call_kwargs = mock_zammad.add_article_with_attachment.call_args.kwargs
    assert call_kwargs["ticket_id"] == ZAMMAD_TICKET_ID
    assert call_kwargs["filename"] == "screenshot.jpg"
    assert call_kwargs["content"] == b"fake jpeg bytes"


# ── Steps 7-8: Agent reply → Telegram (with attachment) ──────────────────────

@pytest.mark.asyncio
async def test_step7_agent_reply_forwarded_to_telegram():
    """Agent's public article is forwarded verbatim to the Telegram user."""
    db_ticket = _make_db_ticket()
    db_ticket.telegram_id = TG_ID

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()

    mock_zammad = MagicMock()

    service = NotificationService(mock_bot, mock_zammad)

    payload = ZammadWebhookPayload(
        ticket=WebhookTicket(
            id=ZAMMAD_TICKET_ID,
            number=ZAMMAD_TICKET_NUMBER,
            title="Test",
            state={"name": "open"},
        ),
        article=WebhookArticle(
            id=ZAMMAD_ARTICLE_ID + 1,  # new article, not in bot_article table
            ticket_id=ZAMMAD_TICKET_ID,
            body="Добрый день! Изучаем ваш вопрос.",
            internal=False,
            created_by_id=99,  # agent, not integration user
            content_type="text/plain",
            attachments=[],
        ),
    )

    patches = _patch_session(ticket=db_ticket, is_bot_article=False)
    *ctx_managers, _, mock_ticket_repo, mock_idem_repo = patches
    mock_ticket_repo.get_by_zammad_id = AsyncMock(return_value=db_ticket)

    with (
        ctx_managers[6], ctx_managers[7], ctx_managers[8], ctx_managers[9],
    ):
        await service.handle_webhook(payload)

    mock_bot.send_message.assert_called_once()
    sent_kwargs = mock_bot.send_message.call_args.kwargs
    assert sent_kwargs["chat_id"] == TG_ID
    assert "Добрый день" in sent_kwargs["text"]
    assert ZAMMAD_TICKET_NUMBER in sent_kwargs["text"]


@pytest.mark.asyncio
async def test_step8_agent_attachment_forwarded_to_telegram():
    """Agent's attachment is downloaded from Zammad and sent to Telegram."""
    db_ticket = _make_db_ticket()
    db_ticket.telegram_id = TG_ID

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.send_document = AsyncMock()

    mock_zammad = MagicMock()
    mock_zammad.download_attachment = AsyncMock(return_value=b"pdf content")

    service = NotificationService(mock_bot, mock_zammad)

    attachment = ZammadAttachmentSchema(
        id=500,
        filename="invoice.pdf",
        size=1024,
        preferences={"Content-Type": "application/pdf"},
    )

    payload = ZammadWebhookPayload(
        ticket=WebhookTicket(
            id=ZAMMAD_TICKET_ID,
            number=ZAMMAD_TICKET_NUMBER,
            title="Test",
            state={"name": "open"},
        ),
        article=WebhookArticle(
            id=ZAMMAD_ARTICLE_ID + 2,
            ticket_id=ZAMMAD_TICKET_ID,
            body="Прикладываю счёт.",
            internal=False,
            created_by_id=99,
            content_type="text/plain",
            attachments=[attachment],
        ),
    )

    patches = _patch_session(ticket=db_ticket, is_bot_article=False)
    *ctx_managers, _, mock_ticket_repo, _ = patches
    mock_ticket_repo.get_by_zammad_id = AsyncMock(return_value=db_ticket)

    with (
        ctx_managers[6], ctx_managers[7], ctx_managers[8], ctx_managers[9],
    ):
        await service.handle_webhook(payload)

    mock_bot.send_document.assert_called_once()
    mock_zammad.download_attachment.assert_called_once_with(
        ZAMMAD_TICKET_ID, ZAMMAD_ARTICLE_ID + 2, 500
    )


# ── Step 9: Internal note must NOT reach Telegram ────────────────────────────

@pytest.mark.asyncio
async def test_step9_internal_note_not_forwarded():
    """internal=True articles must be silently dropped."""
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_zammad = MagicMock()
    service = NotificationService(mock_bot, mock_zammad)

    payload = ZammadWebhookPayload(
        ticket=WebhookTicket(id=1, number="1", title="T", state={"name": "open"}),
        article=WebhookArticle(
            id=501, ticket_id=1, body="Это внутренняя заметка",
            internal=True,  # ← must be blocked
            created_by_id=10, content_type="text/plain", attachments=[],
        ),
    )

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.notification_service.get_session", return_value=session_mock), \
         patch("src.services.notification_service.get_settings", return_value=MagicMock(
             zammad_integration_user_id=INTEGRATION_USER_ID
         )):
        await service.handle_webhook(payload)

    mock_bot.send_message.assert_not_called()


# ── Step 10: Bot's own article must NOT loop back ─────────────────────────────

@pytest.mark.asyncio
async def test_step10_bot_article_antiloop():
    """
    If the article ID is in bot_article table, it must NOT be forwarded.
    This prevents: Telegram→Zammad→webhook→Telegram loop.
    """
    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_zammad = MagicMock()
    service = NotificationService(mock_bot, mock_zammad)

    payload = ZammadWebhookPayload(
        ticket=WebhookTicket(id=1, number="1", title="T", state={"name": "open"}),
        article=WebhookArticle(
            id=ZAMMAD_ARTICLE_ID,  # this ID is "in bot_article table"
            ticket_id=1,
            body="Сообщение от пользователя (отправлено ботом)",
            internal=False,
            created_by_id=44,  # not the integration user — only DB check saves us
            content_type="text/plain",
            attachments=[],
        ),
    )

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.is_bot_article = AsyncMock(return_value=True)  # ← in DB

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.notification_service.get_session", return_value=session_mock), \
         patch("src.services.notification_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.notification_service.get_settings", return_value=MagicMock(
             zammad_integration_user_id=INTEGRATION_USER_ID
         )):
        await service.handle_webhook(payload)

    mock_bot.send_message.assert_not_called()


# ── Step 11: Ticket closed → user notified ───────────────────────────────────

@pytest.mark.asyncio
async def test_step11_ticket_close_notifies_user():
    """When ticket transitions to 'closed', Telegram user receives a notification."""
    db_ticket = _make_db_ticket()
    db_ticket.telegram_id = TG_ID

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_zammad = MagicMock()
    service = NotificationService(mock_bot, mock_zammad)

    payload = ZammadWebhookPayload(
        ticket=WebhookTicket(
            id=ZAMMAD_TICKET_ID,
            number=ZAMMAD_TICKET_NUMBER,
            title="Test",
            state={"name": "closed"},  # ← ticket is now closed
        ),
        article=WebhookArticle(
            id=ZAMMAD_ARTICLE_ID + 10,
            ticket_id=ZAMMAD_TICKET_ID,
            body="Вопрос решён, закрываем.",
            internal=False,
            created_by_id=99,
            content_type="text/plain",
            attachments=[],
        ),
    )

    patches = _patch_session(ticket=db_ticket, is_bot_article=False)
    *ctx_managers, _, mock_ticket_repo, _ = patches
    mock_ticket_repo.get_by_zammad_id = AsyncMock(return_value=db_ticket)

    with (
        ctx_managers[6], ctx_managers[7], ctx_managers[8], ctx_managers[9],
    ):
        await service.handle_webhook(payload)

    # Two messages: agent reply body + closure notification
    assert mock_bot.send_message.call_count == 2
    close_msg = mock_bot.send_message.call_args_list[-1].kwargs["text"]
    assert "закрыт" in close_msg
    assert ZAMMAD_TICKET_NUMBER in close_msg


# ── Step 12: New message after closure → NEW ticket ──────────────────────────

@pytest.mark.asyncio
async def test_step12_new_ticket_after_closure():
    """
    When the active ticket is closed, the next user message must trigger
    creation of a NEW ticket — the old chain must not be broken.
    """
    closed_db_ticket = _make_db_ticket(status=TicketStatus.open, is_active=True)
    new_db_ticket = _make_db_ticket(status=TicketStatus.open, is_active=True)
    new_db_ticket.zammad_ticket_number = "200002"

    closed_zammad_ticket = _make_zammad_ticket(state_name="closed")
    new_zammad_ticket = ZammadTicketSchema(
        id=ZAMMAD_TICKET_ID + 1,
        number="200002",
        title="Новое обращение",
        state={"name": "open"},
        group={"name": "Support L1"},
        customer_id=ZAMMAD_USER_ID,
    )

    mock_zammad = MagicMock()
    # First get_ticket call returns the CLOSED ticket (status sync)
    mock_zammad.get_ticket = AsyncMock(return_value=closed_zammad_ticket)
    # New ticket creation
    mock_zammad.create_ticket = AsyncMock(return_value=new_zammad_ticket)

    service = TicketService(mock_zammad)

    mock_ticket_repo = MagicMock()
    mock_ticket_repo.get_active = AsyncMock(return_value=closed_db_ticket)
    mock_ticket_repo.update_status = AsyncMock(return_value=closed_db_ticket)
    mock_ticket_repo.create = AsyncMock(return_value=new_db_ticket)

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)

    with patch("src.services.ticket_service.get_session", return_value=session_mock), \
         patch("src.services.ticket_service.TicketRepository", return_value=mock_ticket_repo), \
         patch("src.services.ticket_service.IdempotencyRepository", return_value=mock_idem_repo):
        ticket, created = await service.get_or_create_ticket(
            telegram_id=TG_ID,
            zammad_user_id=ZAMMAD_USER_ID,
            queue=QueueType.support,
            initial_message="Снова здравствуйте, новый вопрос",
        )

    # NEW ticket was created — old closed chain preserved
    assert created is True
    assert ticket.number == "200002"
    mock_zammad.create_ticket.assert_called_once()
    # Old ticket was deactivated
    mock_ticket_repo.update_status.assert_called_once()
