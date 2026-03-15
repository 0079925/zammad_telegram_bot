"""
FastAPI router for the Zammad webhook endpoint.

Security:
    Zammad sends the webhook with an Authorization header:
        Authorization: Bearer <ZAMMAD_WEBHOOK_SECRET>
    We validate the secret on every request and return 401 on mismatch.

Anti-loop:
    See NotificationService  it checks created_by_id and bot_article table.
"""
from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError

from src.config import get_settings
from src.services.notification_service import NotificationService
from src.zammad.schemas import ZammadWebhookPayload

logger = structlog.get_logger(__name__)

router = APIRouter()


def _get_notification_service(request: Request) -> NotificationService:
    return request.app.state.notification_service


def _verify_secret(authorization: str | None = Header(default=None)) -> None:
    cfg = get_settings()
    expected = f"Bearer {cfg.zammad_webhook_secret.get_secret_value()}"
    if authorization != expected:
        logger.warning("webhook_unauthorized", has_header=authorization is not None)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(_verify_secret)],
)
async def zammad_webhook(
    request: Request,
    notification_service: NotificationService = Depends(_get_notification_service),
) -> dict:
    correlation_id = str(uuid.uuid4())
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    logger.info(
        "webhook_received",
        has_article=isinstance(body, dict) and body.get("article") is not None,
        keys=list(body.keys()) if isinstance(body, dict) else None,
        ticket_state=body.get("ticket", {}).get("state") if isinstance(body, dict) else None,
        ticket_id=body.get("ticket", {}).get("id") if isinstance(body, dict) else None,
        ticket_group=body.get("ticket", {}).get("group") if isinstance(body, dict) else None,
        article_internal=body.get("article", {}).get("internal") if isinstance(body, dict) and body.get("article") else None,
        article_created_by_id=body.get("article", {}).get("created_by_id") if isinstance(body, dict) and body.get("article") else None,
    )

    try:
        payload = ZammadWebhookPayload.model_validate(body)
    except ValidationError as exc:
        logger.warning("webhook_payload_invalid", errors=exc.errors())
        raise HTTPException(status_code=422, detail="Invalid payload")

    try:
        await notification_service.handle_webhook(payload, correlation_id=correlation_id)
    except Exception as exc:
        logger.error("webhook_handler_error", error=str(exc), correlation_id=correlation_id)
        return {"status": "error", "detail": "internal error"}

    structlog.contextvars.unbind_contextvars("correlation_id")
    return {"status": "ok"}
