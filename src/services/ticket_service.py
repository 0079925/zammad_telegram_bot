"""
TicketService — manages Zammad ticket lifecycle driven by Telegram events.

Responsibilities:
    - Find or create a ticket for a user+queue
    - Add text/attachment articles to an existing ticket
    - Track article IDs created by the bot (loop prevention)
    - Handle ticket status transitions
"""
from __future__ import annotations

import structlog

from src.config import get_settings
from src.db.models import QueueType, TicketStatus
from src.db.repositories import IdempotencyRepository, TicketRepository
from src.db.session import get_session
from src.zammad.client import ZammadClient
from src.zammad.schemas import ZammadTicketSchema

logger = structlog.get_logger(__name__)

_CLOSED_STATUSES = {TicketStatus.closed, TicketStatus.merged}


def _queue_to_group(queue: QueueType) -> str:
    cfg = get_settings()
    return cfg.zammad_group_support if queue == QueueType.support else cfg.zammad_group_manager


def _zammad_state_to_status(state_name: str) -> TicketStatus:
    mapping = {
        "new": TicketStatus.new,
        "open": TicketStatus.open,
        "pending reminder": TicketStatus.pending_reminder,
        "pending action": TicketStatus.pending_action,
        "closed": TicketStatus.closed,
        "merged": TicketStatus.merged,
    }
    return mapping.get(state_name.lower(), TicketStatus.open)


def _status_display(status: TicketStatus) -> str:
    labels = {
        TicketStatus.new: "🆕 Новый",
        TicketStatus.open: "🟢 Открыт",
        TicketStatus.pending_reminder: "⏳ Ожидает",
        TicketStatus.pending_action: "⏳ На рассмотрении",
        TicketStatus.closed: "🔴 Закрыт",
        TicketStatus.merged: "🔀 Объединён",
    }
    return labels.get(status, status.value)


class TicketService:
    def __init__(self, zammad: ZammadClient) -> None:
        self._zammad = zammad

    # ── Open / get active ticket ──────────────────────────────────────────────

    async def get_or_create_ticket(
        self,
        *,
        telegram_id: int,
        zammad_user_id: int,
        queue: QueueType,
        initial_message: str,
        correlation_id: str | None = None,
    ) -> tuple[ZammadTicketSchema, bool]:
        """
        Returns (ticket, created_flag).
        If there is an active, non-closed ticket — returns it.
        Otherwise creates a new one.
        """
        log = logger.bind(telegram_id=telegram_id, queue=queue.value, correlation_id=correlation_id)

        async with get_session() as session:
            repo = TicketRepository(session)
            existing = await repo.get_active(telegram_id, queue)

            if existing and existing.status not in _CLOSED_STATUSES:
                # Refresh status from Zammad
                zammad_ticket = await self._zammad.get_ticket(existing.zammad_ticket_id)
                real_status = _zammad_state_to_status(
                    (zammad_ticket.state or {}).get("name", "open")
                )
                if real_status in _CLOSED_STATUSES:
                    await repo.update_status(existing.zammad_ticket_id, real_status)
                    # Fall through to creation below
                else:
                    log.info("ticket_reused", zammad_ticket_id=existing.zammad_ticket_id)
                    return zammad_ticket, False

            # Create a new ticket in Zammad
            group = _queue_to_group(queue)
            subject = "Обращение через Telegram"
            zammad_ticket = await self._zammad.create_ticket(
                title=subject,
                group=group,
                customer_id=zammad_user_id,
                body=initial_message,
            )
            idem_repo = IdempotencyRepository(session)
            db_ticket = await repo.create(
                telegram_id=telegram_id,
                zammad_ticket_id=zammad_ticket.id,
                zammad_ticket_number=zammad_ticket.number,
                queue_type=queue,
            )
            # The first article was created with the ticket — record it
            # We don't have the article ID here; Zammad returns it separately.
            # We mark the ticket as "initial article pending" by convention:
            # the article body equals initial_message, so we won't forward it.
            await idem_repo.write_log(
                event_type="ticket_created",
                telegram_id=telegram_id,
                zammad_ticket_id=zammad_ticket.id,
                correlation_id=correlation_id,
                payload={"number": zammad_ticket.number, "group": group, "queue": queue.value},
            )
            log.info(
                "ticket_created",
                zammad_ticket_id=zammad_ticket.id,
                number=zammad_ticket.number,
            )
            return zammad_ticket, True

    # ── Add messages ──────────────────────────────────────────────────────────

    async def add_text_article(
        self,
        *,
        telegram_id: int,
        queue: QueueType,
        text: str,
        correlation_id: str | None = None,
    ) -> bool:
        """Add a text article to the active ticket.  Returns False if no ticket."""
        async with get_session() as session:
            repo = TicketRepository(session)
            ticket = await repo.get_active(telegram_id, queue)
            if not ticket:
                return False

            article = await self._zammad.add_article(
                ticket_id=ticket.zammad_ticket_id, body=text
            )
            await repo.record_bot_article(article.id, ticket.id)
            await IdempotencyRepository(session).write_log(
                event_type="article_sent",
                telegram_id=telegram_id,
                zammad_ticket_id=ticket.zammad_ticket_id,
                correlation_id=correlation_id,
            )
        return True

    async def add_attachment_article(
        self,
        *,
        telegram_id: int,
        queue: QueueType,
        caption: str,
        filename: str,
        content: bytes,
        content_type: str,
        correlation_id: str | None = None,
    ) -> bool:
        """Upload a file attachment to the active ticket. Returns False if no ticket."""
        async with get_session() as session:
            repo = TicketRepository(session)
            ticket = await repo.get_active(telegram_id, queue)
            if not ticket:
                return False

            article = await self._zammad.add_article_with_attachment(
                ticket_id=ticket.zammad_ticket_id,
                body=caption or "📎 Вложение из Telegram",
                filename=filename,
                content=content,
                content_type=content_type,
            )
            await repo.record_bot_article(article.id, ticket.id)
            await IdempotencyRepository(session).write_log(
                event_type="attachment_sent",
                telegram_id=telegram_id,
                zammad_ticket_id=ticket.zammad_ticket_id,
                correlation_id=correlation_id,
                payload={"filename": filename, "content_type": content_type},
            )
        return True

    # ── Status helpers ────────────────────────────────────────────────────────

    async def get_active_ticket_info(
        self, telegram_id: int, queue: QueueType
    ) -> tuple[str, str] | None:
        """Return (ticket_number, status_label) or None."""
        async with get_session() as session:
            repo = TicketRepository(session)
            ticket = await repo.get_active(telegram_id, queue)
            if not ticket:
                return None
            return ticket.zammad_ticket_number, _status_display(ticket.status)

    async def sync_ticket_status(self, zammad_ticket_id: int) -> TicketStatus | None:
        """Pull current status from Zammad and persist it."""
        try:
            zammad_ticket = await self._zammad.get_ticket(zammad_ticket_id)
        except Exception:
            return None
        state_name = (zammad_ticket.state or {}).get("name", "open")
        status = _zammad_state_to_status(state_name)
        async with get_session() as session:
            repo = TicketRepository(session)
            updated = await repo.update_status(zammad_ticket_id, status)
        return updated.status if updated else None

    def status_display(self, status: TicketStatus) -> str:
        return _status_display(status)
