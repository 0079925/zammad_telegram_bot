"""
Application entry-point.

Architecture notes
──────────────────
Both the aiogram polling loop and the FastAPI/uvicorn webhook server run in the
SAME asyncio event loop via asyncio.gather().  This is mandatory because:

  • NotificationService holds a reference to the aiogram Bot object.
  • Bot.send_message() is bound to the event loop it was created in.
  • Running uvicorn in a separate thread (separate loop) would make every
    bot.send_message() call inside the webhook handler cross loop boundaries,
    resulting in a "Future attached to different loop" RuntimeError at the
    exact moment the first agent reply arrives.

Startup sequence
────────────────
  1. Configure structured logging
  2. Open async DB engine (SQLAlchemy) — connection pool warms up lazily
  3. Connect to Redis (FSM storage + idempotency cache)
  4. Open Zammad HTTP client (persistent httpx.AsyncClient)
  5. Build aiogram Bot + Dispatcher (middlewares, routers, DI)
  6. Build FastAPI app; attach NotificationService to app.state
  7. asyncio.gather(uvicorn_server.serve(), dp.start_polling(bot))

Graceful shutdown
─────────────────
  SIGTERM / SIGINT  →  signal handler sets a shared asyncio.Event
                    →  polling loop stops (dp.stop_polling())
                    →  uvicorn server stops (server.should_exit = True)
                    →  both coroutines return  →  cleanup block runs
"""
from __future__ import annotations

import asyncio
import signal

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


async def main() -> None:
    cfg = get_settings()
    configure_logging(cfg.log_level, cfg.is_development)

    logger.info(
        "startup",
        environment=cfg.environment,
        webhook_port=cfg.app_port,
        webhook_path=cfg.zammad_webhook_path,
    )

    # ── Infrastructure ────────────────────────────────────────────────────────
    redis = Redis.from_url(cfg.redis_url, decode_responses=False)

    async with ZammadClient(cfg) as zammad:

        # ── Bot ───────────────────────────────────────────────────────────────
        bot = create_bot(cfg)
        dispatcher = create_dispatcher(cfg, redis)
        bot, dispatcher = await build_application(cfg, bot, dispatcher, zammad)

        # ── Webhook server ─────────────────────────────────────────────────────
        # NotificationService shares the SAME bot instance → same event loop ✓
        notification_service = NotificationService(bot, zammad)
        webhook_app = create_webhook_app(cfg)
        webhook_app.state.notification_service = notification_service

        uvicorn_config = uvicorn.Config(
            app=webhook_app,
            host=cfg.app_host,
            port=cfg.app_port,
            log_config=None,   # structlog handles all logging
            access_log=False,
        )
        server = uvicorn.Server(uvicorn_config)

        # ── Graceful shutdown ─────────────────────────────────────────────────
        stop_event: asyncio.Event = asyncio.Event()

        def _on_signal(*_) -> None:
            logger.info("shutdown_signal_received")
            stop_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _on_signal)
            except (NotImplementedError, ValueError):
                # Windows: add_signal_handler not supported for all signals
                signal.signal(sig, _on_signal)

        async def _watch_stop() -> None:
            """Stop both services once the shutdown event fires."""
            await stop_event.wait()
            logger.info("stopping_services")
            # Stop aiogram polling
            await dispatcher.stop_polling()
            # Stop uvicorn
            server.should_exit = True

        # ── Run everything concurrently in the same event loop ────────────────
        logger.info("bot_polling_starting")
        logger.info("webhook_server_starting", host=cfg.app_host, port=cfg.app_port)

        try:
            await asyncio.gather(
                server.serve(),
                dispatcher.start_polling(
                    bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                ),
                _watch_stop(),
            )
        finally:
            logger.info("cleanup_starting")
            await bot.session.close()
            await redis.aclose()
            await close_engine()
            logger.info("shutdown_complete")


if __name__ == "__main__":
    asyncio.run(main())
