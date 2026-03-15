"""Repository: Ticket + BotArticle CRUD."""
from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import BotArticle, QueueType, Ticket, TicketStatus


class TicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Ticket ────────────────────────────────────────────────────────────────

    async def get_active(self, telegram_id: int, queue: QueueType) -> Ticket | None:
        """Return the currently active ticket for a user+queue combo."""
        result = await self._session.execute(
            select(Ticket).where(
                Ticket.telegram_id == telegram_id,
                Ticket.queue_type == queue,
                Ticket.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def get_active_any(self, telegram_id: int) -> Ticket | None:
        """Return the latest active ticket across all queues."""
        result = await self._session.execute(
            select(Ticket)
            .where(
                Ticket.telegram_id == telegram_id,
                Ticket.is_active == True,  # noqa: E712
            )
            .order_by(desc(Ticket.updated_at))
        )
        return result.scalars().first()

    async def get_by_zammad_id(self, zammad_ticket_id: int) -> Ticket | None:
        """Return the most recent ticket for a given Zammad ticket ID."""
        result = await self._session.execute(
            select(Ticket)
            .where(Ticket.zammad_ticket_id == zammad_ticket_id)
            .order_by(desc(Ticket.created_at))
        )
        return result.scalars().first()

    async def list_recent(self, telegram_id: int, limit: int = 10) -> list[Ticket]:
        result = await self._session.execute(
            select(Ticket)
            .where(Ticket.telegram_id == telegram_id)
            .order_by(desc(Ticket.updated_at))
            .limit(limit)
        )
        return list(result.scalars().all())

    async def activate_by_zammad_id(self, telegram_id: int, zammad_ticket_id: int) -> Ticket | None:
        target = await self.get_by_zammad_id(zammad_ticket_id)
        if target is None or target.telegram_id != telegram_id:
            return None

        active_same_queue = await self.get_active(telegram_id, target.queue_type)
        if active_same_queue and active_same_queue.id != target.id:
            active_same_queue.is_active = False

        if target.status not in (TicketStatus.closed, TicketStatus.merged):
            target.is_active = True

        return target

    async def create(
        self,
        *,
        telegram_id: int,
        zammad_ticket_id: int,
        zammad_ticket_number: str,
        queue_type: QueueType,
    ) -> Ticket:
        # Deactivate any previous active ticket for this queue
        existing = await self.get_active(telegram_id, queue_type)
        if existing:
            existing.is_active = False

        ticket = Ticket(
            id=uuid.uuid4(),
            telegram_id=telegram_id,
            zammad_ticket_id=zammad_ticket_id,
            zammad_ticket_number=zammad_ticket_number,
            queue_type=queue_type,
            status=TicketStatus.open,
            is_active=True,
        )
        self._session.add(ticket)
        return ticket

    async def update_status(
        self, zammad_ticket_id: int, status: TicketStatus
    ) -> Ticket | None:
        ticket = await self.get_by_zammad_id(zammad_ticket_id)
        if ticket:
            ticket.status = status
            if status in (TicketStatus.closed, TicketStatus.merged):
                ticket.is_active = False
                from datetime import datetime, timezone

                ticket.closed_at = datetime.now(timezone.utc)
        return ticket

    async def deactivate(self, ticket_id: uuid.UUID) -> None:
        result = await self._session.execute(
            select(Ticket).where(Ticket.id == ticket_id)
        )
        ticket = result.scalar_one_or_none()
        if ticket:
            ticket.is_active = False

    # ── BotArticle ────────────────────────────────────────────────────────────

    async def record_bot_article(self, article_id: int, ticket_id: uuid.UUID) -> None:
        """Mark an article as bot-created so we can skip it on webhook delivery."""
        self._session.add(BotArticle(article_id=article_id, ticket_id=ticket_id))

    async def is_bot_article(self, article_id: int) -> bool:
        result = await self._session.execute(
            select(BotArticle).where(BotArticle.article_id == article_id)
        )
        return result.scalar_one_or_none() is not None
