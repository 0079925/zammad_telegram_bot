"""
main.py — FastAPI application entry point.
"""

import logging

from fastapi import FastAPI
from contextlib import asynccontextmanager

from app.database import init_db
from app.config import settings
from app.routers.telegram import router as telegram_router
from app.routers.zammad_webhook import router as zammad_router
from app import telegram_client as tg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    log.info("Initialising database...")
    await init_db()
    log.info("Database ready")

    # Register Telegram webhook if URL configured
    if settings.telegram_webhook_url:
        ok = await tg.register_webhook(settings.telegram_webhook_url)
        if ok:
            log.info("Telegram webhook registered: %s", settings.telegram_webhook_url)
        else:
            log.warning("Failed to register Telegram webhook")
    else:
        log.warning("TELEGRAM_WEBHOOK_URL not set — webhook not registered")

    yield
    # Shutdown (nothing to clean up for SQLite)
    log.info("Shutting down")


app = FastAPI(
    title="Zammad Telegram Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(telegram_router)
app.include_router(zammad_router)


@app.get("/")
async def root():
    return {"service": "zammad-telegram-gateway", "status": "running"}
