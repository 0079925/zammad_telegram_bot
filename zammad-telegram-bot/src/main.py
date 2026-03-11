"""
Application entry-point.

Starts:
    1. PostgreSQL connection pool
    2. Zammad HTTP client
    3. FastAPI webhook server (uvicorn in background thread)
    4. aiogram bot (polling or webhook mode)

Graceful shutdown:
    SIGTERM / SIGINT → stops the bot loop → stops uvicorn → closes DB pool.
"""
from __future__ import annotations

import asyncio
import signal
import threading
from contextlib import asynccontextmanager

import structlog
import uvicorn
from redis.asyncio import Redis

from src.bot.app import build_application, create_bot, create_dispatcher
from src.config import get_settings
from src.db.session import close_engine
from src.logging_config import configure_logging
from src.services.notification_service import NotificationService
from src.webhook.app import create_webhook_app
from src.zammad.client import ZammadClient

logger = structlog.get_logger(__name__)


def _run_uvicorn(app, host: str, port: int, stop_event: threading.Event) -> None:
    """Run uvicorn in a daemon thread; stop when stop_event is set."""
    config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_config=None,  # We use structlog
        access_log=False,
    )
    server = uvicorn.Server(config)

    async def _serve() -> None:
        await server.serve()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _on_stop() -> None:
        server.should_exit = True

    stop_event_check_task = None

    async def _watch_stop() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
        _on_stop()

    loop.run_until_complete(
        asyncio.gather(_serve(), _watch_stop())
    )
    loop.close()


async def main() -> None:
    cfg = get_settings()
    configure_logging(cfg.log_level, cfg.is_development)

    logger.info(
        "starting",
        environment=cfg.environment,
        app_port=cfg.app_port,
    )

    redis = Redis.from_url(cfg.redis_url, decode_responses=False)
    stop_event = threading.Event()

    async with ZammadClient(cfg) as zammad:
        bot = create_bot(cfg)
        dispatcher = create_dispatcher(cfg, redis)
        bot, dispatcher = await build_application(cfg, bot, dispatcher, zammad)

        # Attach NotificationService to webhook app via app.state
        notification_service = NotificationService(bot, zammad)
        webhook_app = create_webhook_app(cfg)
        webhook_app.state.notification_service = notification_service

        # Start webhook server in background thread
        uvicorn_thread = threading.Thread(
            target=_run_uvicorn,
            args=(webhook_app, cfg.app_host, cfg.app_port, stop_event),
            daemon=True,
        )
        uvicorn_thread.start()
        logger.info("webhook_server_started", host=cfg.app_host, port=cfg.app_port)

        # Graceful shutdown on SIGTERM / SIGINT
        loop = asyncio.get_running_loop()

        def _shutdown(*_) -> None:
            logger.info("shutdown_signal_received")
            stop_event.set()
            loop.stop()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _shutdown)
            except NotImplementedError:
                # Windows does not support add_signal_handler for all signals
                signal.signal(sig, _shutdown)

        try:
            if cfg.telegram_webhook_url:
                # Webhook mode — aiogram receives updates via FastAPI
                logger.info("bot_starting_webhook_mode", url=cfg.telegram_webhook_url)
                # In webhook mode the Telegram updates are delivered to a
                # separate path; wire it up inside the webhook app.
                # For simplicity this project defaults to polling.
                await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
            else:
                logger.info("bot_starting_polling_mode")
                await dispatcher.start_polling(
                    bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                )
        finally:
            logger.info("bot_stopped")
            stop_event.set()
            await bot.session.close()
            await redis.aclose()
            await close_engine()
            logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
