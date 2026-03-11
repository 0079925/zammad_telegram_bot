"""
Deduplication middleware.

Stores processed Telegram update IDs in PostgreSQL.
On restart or duplicate delivery, the same update is silently dropped.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from src.db.repositories import IdempotencyRepository
from src.db.session import get_session

logger = structlog.get_logger(__name__)


class DeduplicationMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)

        update_id = event.update_id
        async with get_session() as session:
            repo = IdempotencyRepository(session)
            if await repo.is_processed(update_id):
                logger.debug("duplicate_update_dropped", update_id=update_id)
                return None
            await repo.mark_processed(update_id)

        return await handler(event, data)
