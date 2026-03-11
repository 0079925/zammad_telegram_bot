"""Repository: ProcessedUpdate — deduplication of Telegram update IDs."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import IntegrationLog, ProcessedUpdate


class IdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_processed(self, update_id: int) -> bool:
        result = await self._session.execute(
            select(ProcessedUpdate).where(ProcessedUpdate.update_id == update_id)
        )
        return result.scalar_one_or_none() is not None

    async def mark_processed(self, update_id: int) -> None:
        self._session.add(ProcessedUpdate(update_id=update_id))

    async def write_log(
        self,
        *,
        event_type: str,
        telegram_id: int | None = None,
        zammad_ticket_id: int | None = None,
        correlation_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        self._session.add(
            IntegrationLog(
                event_type=event_type,
                telegram_id=telegram_id,
                zammad_ticket_id=zammad_ticket_id,
                correlation_id=correlation_id,
                payload=payload,
            )
        )
