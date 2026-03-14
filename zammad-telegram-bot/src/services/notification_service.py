"""
NotificationService — forwards agent replies from Zammad to Telegram.

Called by the webhook handler when a new article or ticket state change arrives.

Anti-loop logic (article-based webhooks):
    1. article.internal == True                         → skip (internal note)
    2. article.created_by_id == integration_user_id    → skip (our own message)
    3. article_id is in bot_article table               → skip (belt-and-suspenders)

State-only webhooks (article is None):
    - Update ticket status in DB
    - Notify user if the status transitioned to/from closed
    - Requires a dedicated Zammad Trigger (see ARCHITECTURE.md § State-only trigger)

Attachment forwarding:
    - Downloads each attachment from Zammad and sends to Telegram as a file.
    - Falls back to a text message if download fails.
"""
from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from src.config import get_settings
from src.db.models import QueueType, Ticket, TicketStatus
from src.db.repositories import IdempotencyRepository, TicketRepository
from src.db.session import get_session
from src.zammad.client import ZammadClient
from src.zammad.schemas import ZammadWebhookPayload

logger = structlog.get_logger(__name__)

_CLOSED_STATUSES = {TicketStatus.closed, TicketStatus.merged}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_state_name(state) -> str:
    """Safely extract state name from Zammad's state field (dict or str)."""
    if isinstance(state, dict):
        return state.get("name", "open")
    if isinstance(state, str):
        return state
    return "open"


def _queue_label(queue_type: QueueType) -> str:
    """Human-readable label for notifications."""
    return "👔 Менеджер" if queue_type == QueueType.manager else "💬 Поддержка"


def _reply_keyboard(queue_type: QueueType) -> InlineKeyboardMarkup:
    """Inline button letting the user switch to the right queue context to reply."""
    cb = f"queue:{queue_type.value}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="↩️ Ответить в этот тикет", callback_data=cb)]
        ]
    )


# ── Service ───────────────────────────────────────────────────────────────────

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

        Two modes:
          • article is None  → state-change-only event (ticket closed/reopened without comment)
          • article present  → new article from agent; forward to Telegram after anti-loop checks
        """
        cfg = get_settings()
        log = logger.bind(
            zammad_ticket_id=payload.ticket.id,
            correlation_id=correlation_id,
        )

        article = payload.article
        state_name = _parse_state_name(payload.ticket.state)

        from src.services.ticket_service import _zammad_state_to_status
        new_status = _zammad_state_to_status(state_name)

        # ── Mode A: state-only webhook (no article) ───────────────────────────
        if article is None:
            log.debug("webhook_state_only", new_status=new_status.value)
            await self._handle_state_change(payload, new_status, log, correlation_id)
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
        async with get_session() as session:
            repo = TicketRepository(session)
            await repo.update_status(payload.ticket.id, new_status)

        # ── Forward text body ─────────────────────────────────────────────────
        body = article.body_text
        ql = _queue_label(db_ticket.queue_type)
        if body:
            header = f"{ql} <b>— ответ агента</b>\n🎫 Тикет #{payload.ticket.number}\n\n"
            await self._bot.send_message(
                chat_id=telegram_id,
                text=header + body,
                parse_mode="HTML",
                reply_markup=_reply_keyboard(db_ticket.queue_type),
            )

        # ── Forward attachments ───────────────────────────────────────────────
        for attachment in article.attachments:
            await self._forward_attachment(
                telegram_id=telegram_id,
                ticket_id=payload.ticket.id,
                article_id=article.id,
                attachment=attachment,
                queue_type=db_ticket.queue_type,
                correlation_id=correlation_id,
            )

        # ── Notify if ticket was closed ───────────────────────────────────────
        if new_status in _CLOSED_STATUSES:
            await self._send_closed_notification(telegram_id, payload.ticket.number, db_ticket.queue_type)

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

    async def _handle_state_change(
        self,
        payload: ZammadWebhookPayload,
        new_status: TicketStatus,
        log,
        correlation_id: str | None,
    ) -> None:
        """Handle a state-only webhook (no article — ticket was closed/reopened silently)."""
        async with get_session() as session:
            repo = TicketRepository(session)
            db_ticket = await repo.get_by_zammad_id(payload.ticket.id)
            if db_ticket is None:
                log.warning("webhook_state_only_ticket_not_found")
                return

            old_status = db_ticket.status
            await repo.update_status(payload.ticket.id, new_status)

        telegram_id = db_ticket.telegram_id

        # Notify on meaningful transitions only
        if new_status == old_status:
            log.debug("webhook_state_unchanged", status=new_status.value)
            return

        if new_status in _CLOSED_STATUSES:
            await self._send_closed_notification(telegram_id, payload.ticket.number, db_ticket.queue_type)
        elif old_status in _CLOSED_STATUSES and new_status not in _CLOSED_STATUSES:
            # Ticket was reopened
            ql = _queue_label(db_ticket.queue_type)
            await self._bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"🔄 {ql}: тикет <b>#{payload.ticket.number}</b> переоткрыт.\n"
                    "Вы можете продолжить переписку."
                ),
                parse_mode="HTML",
                reply_markup=_reply_keyboard(db_ticket.queue_type),
            )

        log.info(
            "state_change_notified",
            old=old_status.value,
            new=new_status.value,
            telegram_id=telegram_id,
        )

    async def _send_closed_notification(
        self,
        telegram_id: int,
        ticket_number: str,
        queue_type: QueueType,
    ) -> None:
        ql = _queue_label(queue_type)
        await self._bot.send_message(
            chat_id=telegram_id,
            text=(
                f"✅ {ql}: тикет <b>#{ticket_number}</b> закрыт.\n"
                "Если вопрос возник снова — нажмите кнопку ниже для нового обращения."
            ),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(
                        text="🆕 Создать новое обращение",
                        callback_data=f"queue:{queue_type.value}",
                    )]
                ]
            ),
        )

    async def _forward_attachment(
        self,
        *,
        telegram_id: int,
        ticket_id: int,
        article_id: int,
        attachment,
        queue_type: QueueType,
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
            ql = _queue_label(queue_type)
            await self._bot.send_message(
                chat_id=telegram_id,
                text=(
                    f"{ql} — агент прислал файл: <code>{attachment.filename}</code>\n"
                    "(Не удалось скачать — обратитесь в поддержку)"
                ),
                parse_mode="HTML",
            )
