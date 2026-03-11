"""
Correlation ID middleware.

Generates a unique correlation_id per update and binds it to structlog
context so every log line inside the handler carries it automatically.
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class CorrelationMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        correlation_id = str(uuid.uuid4())
        data["correlation_id"] = correlation_id
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        try:
            return await handler(event, data)
        finally:
            structlog.contextvars.unbind_contextvars("correlation_id")
