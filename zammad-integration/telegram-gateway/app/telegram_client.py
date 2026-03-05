"""
telegram_client.py — minimal async Telegram Bot API wrapper (sendMessage only).
No aiogram dependency — keeps the image lean.
"""

import logging
import httpx

from app.config import settings

log = logging.getLogger(__name__)

TG_API = f"https://api.telegram.org/bot{settings.telegram_bot_token}"


async def send_message(chat_id: str | int, text: str) -> bool:
    """Send a plain-text message to a Telegram chat."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{TG_API}/sendMessage", json=payload)
            if not r.is_success:
                log.error("Telegram sendMessage failed: %s %s", r.status_code, r.text)
                return False
            return True
    except Exception as e:
        log.error("Telegram sendMessage exception: %s", e)
        return False


async def register_webhook(url: str) -> bool:
    """Register the bot webhook URL with Telegram."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{TG_API}/setWebhook",
                json={"url": url, "allowed_updates": ["message"]},
            )
            data = r.json()
            if data.get("ok"):
                log.info("Telegram webhook registered: %s", url)
                return True
            else:
                log.error("Webhook registration failed: %s", data)
                return False
    except Exception as e:
        log.error("Webhook registration exception: %s", e)
        return False


async def notify_manager(text: str) -> bool:
    """Send an alert to the configured manager chat."""
    chat_id = settings.manager_telegram_chat_id
    if not chat_id:
        log.warning("MANAGER_TELEGRAM_CHAT_ID not set, skipping notification")
        return False
    return await send_message(chat_id, text)
