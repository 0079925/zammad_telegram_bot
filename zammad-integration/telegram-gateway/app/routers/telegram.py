"""
routers/telegram.py

POST /webhook/telegram  — receives updates from Telegram Bot API.

Flow:
  1. User sends message → Telegram → this endpoint
  2. Find existing open ticket for chat_id
  3. If open ticket: add article
  4. If no open ticket: create new ticket
  5. Map chat_id ↔ ticket_id in SQLite
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import ChatTicketMap
from app import zammad_client as zammad
from app import telegram_client as tg

log = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_update(data: dict) -> Optional[tuple[str, str, str, str, str]]:
    """Extract (chat_id, text, first_name, last_name, username) from update."""
    msg = data.get("message")
    if not msg:
        return None
    chat = msg.get("chat", {})
    user = msg.get("from", {})
    text = msg.get("text", "").strip()
    if not text:
        return None
    return (
        str(chat["id"]),
        text,
        user.get("first_name", ""),
        user.get("last_name", ""),
        user.get("username", ""),
    )


async def _get_open_mapping(
    db: AsyncSession, chat_id: str
) -> Optional[ChatTicketMap]:
    result = await db.execute(
        select(ChatTicketMap)
        .where(ChatTicketMap.telegram_chat_id == chat_id)
        .where(ChatTicketMap.status == "open")
        .order_by(ChatTicketMap.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# ── Route ─────────────────────────────────────────────────────────────────────

@router.post("/webhook/telegram")
async def telegram_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    data = await request.json()
    log.debug("Telegram update: %s", data)

    parsed = _extract_update(data)
    if parsed is None:
        # Not a text message — ignore silently
        return {"ok": True}

    chat_id, text, first_name, last_name, username = parsed

    # Find open ticket for this chat
    mapping = await _get_open_mapping(db, chat_id)

    if mapping:
        # Verify ticket is still open in Zammad
        still_open = await zammad.is_ticket_open(mapping.zammad_ticket_id)
        if not still_open:
            mapping.status = "closed"
            await db.commit()
            mapping = None

    if mapping:
        # Append message to existing ticket
        await zammad.add_article(mapping.zammad_ticket_id, text)
        log.info("Added article to ticket %s for chat %s", mapping.zammad_ticket_id, chat_id)
    else:
        # Create new ticket
        customer = await zammad.get_or_create_customer(
            chat_id, first_name, last_name, username
        )
        customer_id = customer["id"]

        # Use first ~80 chars as title
        title = text[:80] + ("…" if len(text) > 80 else "")
        ticket = await zammad.create_ticket(customer_id, title, text)
        ticket_id = ticket["id"]

        # Save mapping
        new_map = ChatTicketMap(
            telegram_chat_id=chat_id,
            zammad_ticket_id=ticket_id,
            zammad_ticket_number=str(ticket.get("number", "")),
            telegram_username=username,
            telegram_first_name=first_name,
            telegram_last_name=last_name,
            zammad_customer_id=customer_id,
            status="open",
        )
        db.add(new_map)
        await db.commit()
        log.info("Created ticket %s for chat %s", ticket_id, chat_id)

        # Confirm receipt to user
        await tg.send_message(
            chat_id,
            f"✅ Ваша заявка #{ticket.get('number')} создана. "
            "Наш специалист ответит вам здесь в Telegram.",
        )

    return {"ok": True}
