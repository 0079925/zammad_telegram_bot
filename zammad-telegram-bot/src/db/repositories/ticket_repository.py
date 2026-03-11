"""Repository: Ticket + BotArticle CRUD."""
from __future__ import annotations

import uuid

from sqlalchemy import select
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

    async def get_by_zammad_id(self, zammad_ticket_id: int) -> Ticket | None:
        result = await self._session.execute(
            select(Ticket).where(Ticket.zammad_ticket_id == zammad_ticket_id)
        )
        return result.scalar_one_or_none()

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
