"""
NotificationService — forwards agent replies from Zammad to Telegram.

Called by the webhook handler when a new article arrives.

Anti-loop logic:
    1. article.internal == True          → skip (internal note)
    2. article.created_by_id == integration_user_id → skip (our own message)
    3. article_id is in bot_article table → skip (belt-and-suspenders)

Attachment forwarding:
    - Downloads each attachment from Zammad and sends to Telegram as a file.
    - Falls back to a text message if download fails.
"""
from __future__ import annotations

import io
import mimetypes

import structlog
from aiogram import Bot
from aiogram.types import BufferedInputFile

from src.config import get_settings
from src.db.models import TicketStatus
from src.db.repositories import IdempotencyRepository, TicketRepository
from src.db.session import get_session
from src.zammad.client import ZammadClient
from src.zammad.schemas import ZammadWebhookPayload

logger = structlog.get_logger(__name__)

_CLOSED_STATUSES = {TicketStatus.closed, TicketStatus.merged}


class NotificationService:
    def __init__(self, bot: Bot, zammad: ZammadClient) -> None:
        self._bot = bot
        self._zammad = zammad

    async def handle_webhook(
        self,
        payload: ZammadWebhookPayload,
        correlation_id: str | None = None,
    ) -> None:
        """
        Main entry-point called by the FastAPI webhook route.
        Processes a Zammad ticket event and (if appropriate) notifies Telegram.
        """
        cfg = get_settings()
        log = logger.bind(
            zammad_ticket_id=payload.ticket.id,
            correlation_id=correlation_id,
        )

        article = payload.article
        if article is None:
            log.debug("webhook_no_article_skipped")
            return

        log = log.bind(article_id=article.id)

        # ── Anti-loop: skip internal notes ────────────────────────────────────
        if article.internal:
            log.debug("webhook_internal_article_skipped")
            return

        # ── Anti-loop: skip messages created by the integration user ──────────
        if article.created_by_id == cfg.zammad_integration_user_id:
            log.debug("webhook_bot_article_skipped_by_user_id")
            return

        # ── Anti-loop: belt-and-suspenders DB check ───────────────────────────
        async with get_session() as session:
            repo = TicketRepository(session)
            if await repo.is_bot_article(article.id):
                log.debug("webhook_bot_article_skipped_by_db")
                return

            db_ticket = await repo.get_by_zammad_id(payload.ticket.id)

        if db_ticket is None:
            log.warning("webhook_ticket_not_found_in_db")
            return

        telegram_id = db_ticket.telegram_id
        log = log.bind(telegram_id=telegram_id)

        # ── Sync ticket status ─────────────────────────────────────────────────
        state_name = (payload.ticket.state or {}).get("name", "open")
        from src.services.ticket_service import _zammad_state_to_status, _status_display

        new_status = _zammad_state_to_status(state_name)
        async with get_session() as session:
            repo = TicketRepository(session)
            await repo.update_status(payload.ticket.id, new_status)

        # ── Forward text body ─────────────────────────────────────────────────
        body = article.body_text
        if body:
            header = f"💬 <b>Ответ от поддержки</b>\n🎫 Тикет #{payload.ticket.number}\n\n"
            await self._bot.send_message(
                chat_id=telegram_id,
                text=header + body,
                parse_mode="HTML",
            )

        # ── Forward attachments ───────────────────────────────────────────────
        for attachment in article.attachments:
            await self._forward_attachment(
                telegram_id=telegram_id,
                ticket_id=payload.ticket.id,
                article_id=article.id,
                attachment=attachment,
                correlation_id=correlation_id,
            )

        # ── Notify if ticket was closed ───────────────────────────────────────
        if new_status in _CLOSED_STATUSES:
            await self._bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"✅ Тикет <b>#{payload.ticket.number}</b> закрыт.\n"
                    "Если вопрос возник снова — нажмите кнопку ниже для нового обращения."
                ),
                parse_mode="HTML",
            )

        async with get_session() as session:
            await IdempotencyRepository(session).write_log(
                event_type="agent_reply_forwarded",
                telegram_id=telegram_id,
                zammad_ticket_id=payload.ticket.id,
                correlation_id=correlation_id,
                payload={"article_id": article.id},
            )
        log.info("agent_reply_forwarded")

    # ── Private ───────────────────────────────────────────────────────────────

    async def _forward_attachment(
        self,
        *,
        telegram_id: int,
        ticket_id: int,
        article_id: int,
        attachment,  # ZammadAttachmentSchema
        correlation_id: str | None,
    ) -> None:
        log = logger.bind(
            telegram_id=telegram_id,
            attachment_id=attachment.id,
            filename=attachment.filename,
            correlation_id=correlation_id,
        )
        try:
            content = await self._zammad.download_attachment(
                ticket_id, article_id, attachment.id
            )
            file = BufferedInputFile(content, filename=attachment.filename)
            ct = attachment.content_type

            if ct.startswith("image/"):
                await self._bot.send_photo(chat_id=telegram_id, photo=file)
            else:
                await self._bot.send_document(chat_id=telegram_id, document=file)

            log.info("attachment_forwarded_to_telegram")
        except Exception as exc:
            log.warning("attachment_forward_failed", error=str(exc))
            await self._bot.send_message(
                chat_id=telegram_id,
                text=f"📎 Агент прислал файл: <code>{attachment.filename}</code>\n"
                     "(Не удалось скачать — обратитесь в поддержку)",
                parse_mode="HTML",
            )
