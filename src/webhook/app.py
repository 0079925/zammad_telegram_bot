"""
FastAPI application factory for the webhook server.

This server runs alongside the aiogram bot polling loop inside the same
process (or as a separate process if needed).

Endpoints:
    POST  /webhook/zammad  — receives Zammad trigger events
    GET   /healthz          — readiness / liveness probe
    GET   /readyz           — same; some k8s/Portainer setups expect this path
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.config import Settings, get_settings
from src.webhook.router import router as zammad_router


def create_webhook_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or get_settings()

    app = FastAPI(
        title="Zammad-Telegram Bot Webhook",
        version="1.0.0",
        docs_url="/docs" if cfg.is_development else None,
        redoc_url=None,
    )

    # Health endpoints — no auth, no secrets
    @app.get("/healthz", tags=["ops"])
    @app.get("/readyz", tags=["ops"])
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.include_router(zammad_router, prefix=cfg.zammad_webhook_path, tags=["zammad"])

    return app
