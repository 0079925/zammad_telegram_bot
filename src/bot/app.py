"""
Bot application factory.

Wires together: Dispatcher, FSM storage (Redis), middlewares, handlers,
and dependency injection (services injected via middleware data dict).
"""
from __future__ import annotations

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from src.bot.handlers import main_router
from src.bot.middleware import CorrelationMiddleware, DeduplicationMiddleware
from src.config import Settings
from src.services import NotificationService, TicketService, UserService
from src.zammad.client import ZammadClient


def _make_service_middleware(
    user_service: UserService,
    ticket_service: TicketService,
):
    """
    Returns a simple middleware that injects service instances into
    handler data dict, making them available as handler parameters.
    """
    from collections.abc import Awaitable, Callable
    from typing import Any

    from aiogram import BaseMiddleware
    from aiogram.types import TelegramObject

    class ServiceMiddleware(BaseMiddleware):
        async def __call__(
            self,
            handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: dict[str, Any],
        ) -> Any:
            data["user_service"] = user_service
            data["ticket_service"] = ticket_service
            return await handler(event, data)

    return ServiceMiddleware()


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


def create_dispatcher(settings: Settings, redis: Redis) -> Dispatcher:
    storage = RedisStorage(redis=redis)
    dp = Dispatcher(storage=storage)
    return dp


async def build_application(
    settings: Settings,
    bot: Bot,
    dispatcher: Dispatcher,
    zammad: ZammadClient,
) -> tuple[Bot, Dispatcher]:
    """
    Set up middlewares and include routers.
    Called once at startup.
    """
    user_service = UserService(zammad)
    ticket_service = TicketService(zammad)

    # Order matters: correlation first, then dedup, then services
    dispatcher.update.outer_middleware(CorrelationMiddleware())
    dispatcher.update.outer_middleware(DeduplicationMiddleware())
    dispatcher.update.middleware(_make_service_middleware(user_service, ticket_service))

    dispatcher.include_router(main_router)

    return bot, dispatcher
