"""
TicketService - manages Zammad ticket lifecycle driven by Telegram events.
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

    async def get_or_create_ticket(
        self,
        *,
        telegram_id: int,
        zammad_user_id: int,
        queue: QueueType,
        initial_message: str,
        correlation_id: str | None = None,
    ) -> tuple[ZammadTicketSchema, bool]:
        log = logger.bind(telegram_id=telegram_id, queue=queue.value, correlation_id=correlation_id)
        async with get_session() as session:
            repo = TicketRepository(session)
            existing = await repo.get_active(telegram_id, queue)

            if existing and existing.status not in _CLOSED_STATUSES:
                zammad_ticket = await self._zammad.get_ticket(existing.zammad_ticket_id)
                real_status = _zammad_state_to_status((zammad_ticket.state or {}).get("name", "open"))
                if real_status in _CLOSED_STATUSES:
                    await repo.update_status(existing.zammad_ticket_id, real_status)
                else:
                    log.info("ticket_reused", zammad_ticket_id=existing.zammad_ticket_id)
                    return zammad_ticket, False

            group = _queue_to_group(queue)
            subject = "Обращение через Telegram"
            zammad_ticket = await self._zammad.create_ticket(
                title=subject,
                group=group,
                customer_id=zammad_user_id,
                body=initial_message,
            )
            idem_repo = IdempotencyRepository(session)
            await repo.create(
                telegram_id=telegram_id,
                zammad_ticket_id=zammad_ticket.id,
                zammad_ticket_number=zammad_ticket.number,
                queue_type=queue,
            )
            await idem_repo.write_log(
                event_type="ticket_created",
                telegram_id=telegram_id,
                zammad_ticket_id=zammad_ticket.id,
                correlation_id=correlation_id,
                payload={"number": zammad_ticket.number, "group": group, "queue": queue.value},
            )
            log.info("ticket_created", zammad_ticket_id=zammad_ticket.id, number=zammad_ticket.number)
            return zammad_ticket, True

    async def add_text_article(
        self,
        *,
        telegram_id: int,
        queue: QueueType,
        text: str,
        correlation_id: str | None = None,
    ) -> bool:
        async with get_session() as session:
            repo = TicketRepository(session)
            ticket = await repo.get_active(telegram_id, queue)
            if not ticket:
                return False

            article = await self._zammad.add_article(ticket_id=ticket.zammad_ticket_id, body=text)
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

    async def close_active_ticket(
        self,
        *,
        telegram_id: int,
        queue: QueueType,
        correlation_id: str | None = None,
    ) -> str | None:
        async with get_session() as session:
            repo = TicketRepository(session)
            ticket = await repo.get_active(telegram_id, queue)
            if not ticket:
                return None

            try:
                await self._zammad._request("PUT", f"/tickets/{ticket.zammad_ticket_id}", json={"state": "closed"})
            except Exception as exc:
                logger.warning("zammad_close_failed", error=str(exc), zammad_ticket_id=ticket.zammad_ticket_id)

            await repo.update_status(ticket.zammad_ticket_id, TicketStatus.closed)
            await IdempotencyRepository(session).write_log(
                event_type="ticket_closed_by_user",
                telegram_id=telegram_id,
                zammad_ticket_id=ticket.zammad_ticket_id,
                correlation_id=correlation_id,
            )
            return ticket.zammad_ticket_number

    async def list_recent_tickets(self, telegram_id: int, limit: int = 8) -> list[dict]:
        async with get_session() as session:
            repo = TicketRepository(session)
            tickets = await repo.list_recent(telegram_id, limit=limit)

        items: list[dict] = []
        for ticket in tickets:
            items.append(
                {
                    "zammad_ticket_id": ticket.zammad_ticket_id,
                    "number": ticket.zammad_ticket_number,
                    "queue": ticket.queue_type,
                    "status": ticket.status,
                    "is_active": ticket.is_active,
                }
            )
        return items

    async def activate_ticket_context(self, telegram_id: int, zammad_ticket_id: int) -> tuple[QueueType, str] | None:
        async with get_session() as session:
            repo = TicketRepository(session)
            ticket = await repo.activate_by_zammad_id(telegram_id, zammad_ticket_id)
            if ticket is None or ticket.status in _CLOSED_STATUSES:
                return None
            return ticket.queue_type, ticket.zammad_ticket_number

    async def get_active_ticket_info(self, telegram_id: int, queue: QueueType) -> tuple[str, str] | None:
        async with get_session() as session:
            repo = TicketRepository(session)
            ticket = await repo.get_active(telegram_id, queue)
            if not ticket:
                return None
            return ticket.zammad_ticket_number, _status_display(ticket.status)

    async def sync_ticket_status(self, zammad_ticket_id: int) -> TicketStatus | None:
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
