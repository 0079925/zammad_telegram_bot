"""
routers/zammad_webhook.py

Receives Zammad trigger webhooks:

  POST /webhook/zammad/article      — agent public reply → send to Telegram
  POST /webhook/zammad/escalation   — SLA breach → notify manager in Telegram
  POST /webhook/zammad/new_ticket   — new ticket in Managers group → add watchers
"""

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import ChatTicketMap
from app import telegram_client as tg
from app import zammad_client as zammad

log = logging.getLogger(__name__)
router = APIRouter()

# ── Auth ──────────────────────────────────────────────────────────────────────

def _verify_secret(x_zammad_webhook_secret: str = Header(default="")):
    if x_zammad_webhook_secret != settings.zammad_webhook_secret:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")


# ── Agent reply → Telegram ───────────────────────────────────────────────────

@router.post("/webhook/zammad/article", dependencies=[Depends(_verify_secret)])
async def on_agent_article(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Triggered by Zammad when agent posts a public article.
    Finds the Telegram chat_id for the ticket and forwards the message.
    """
    data = await request.json()
    log.debug("Zammad article webhook: %s", data)

    ticket_id_str = data.get("ticket_id", "")
    article_body = data.get("article_body", "").strip()
    ticket_number = data.get("ticket_number", "")

    if not ticket_id_str or not article_body:
        return {"ok": True, "skipped": "empty payload"}

    try:
        ticket_id = int(ticket_id_str)
    except ValueError:
        return {"ok": True, "skipped": "invalid ticket_id"}

    # Find Telegram chat for this ticket
    result = await db.execute(
        select(ChatTicketMap)
        .where(ChatTicketMap.zammad_ticket_id == ticket_id)
        .limit(1)
    )
    mapping = result.scalar_one_or_none()

    if not mapping:
        log.warning("No Telegram chat mapped for ticket %s", ticket_id)
        return {"ok": True, "skipped": "no chat mapping"}

    # Strip HTML tags for Telegram (basic)
    import re
    clean_body = re.sub(r"<[^>]+>", "", article_body).strip()

    text = f"📩 <b>Ответ по заявке #{ticket_number}</b>\n\n{clean_body}"
    await tg.send_message(mapping.telegram_chat_id, text)
    log.info("Forwarded agent reply for ticket %s to chat %s", ticket_id, mapping.telegram_chat_id)

    return {"ok": True}


# ── SLA breach → Manager alert ───────────────────────────────────────────────

@router.post("/webhook/zammad/escalation", dependencies=[Depends(_verify_secret)])
async def on_escalation(request: Request):
    """
    Triggered by Zammad SLA breach trigger.
    Sends Telegram notification to the manager.
    """
    data = await request.json()
    log.warning("SLA escalation webhook: %s", data)

    ticket_id = data.get("ticket_id", "?")
    priority = data.get("priority", "?")
    sla_info = data.get("sla", "")

    priority_str = str(priority).lower()

    # Honour SLA_ALERT_P2 flag: only alert if it's P1 or P2 is enabled
    is_p1 = "high" in priority_str or "1" in priority_str
    is_p2 = "normal" in priority_str or "2" in priority_str

    if is_p2 and not settings.sla_alert_p2:
        log.info("P2 SLA breach for ticket %s, alerting disabled", ticket_id)
        return {"ok": True, "skipped": "p2_alerts_disabled"}

    priority_label = "🔴 P1 (HIGH)" if is_p1 else "🟡 P2 (NORMAL)"

    text = (
        f"⚠️ <b>SLA НАРУШЕН</b>\n\n"
        f"Тикет: #{ticket_id}\n"
        f"Приоритет: {priority_label}\n"
        f"SLA: {sla_info}\n\n"
        f"Требуется немедленное внимание!"
    )
    await tg.notify_manager(text)
    return {"ok": True}


# ── New Managers ticket → add watcher ────────────────────────────────────────

@router.post("/webhook/zammad/new_ticket", dependencies=[Depends(_verify_secret)])
async def on_new_ticket(request: Request):
    """
    Can be called when a ticket is routed to Managers group.
    Automatically adds all manager-group agents as watchers.
    """
    data = await request.json()
    ticket_id_str = data.get("ticket_id", "")

    if not ticket_id_str:
        return {"ok": True}

    try:
        ticket_id = int(ticket_id_str)
    except ValueError:
        return {"ok": True}

    managers = await zammad.get_managers_group_agents()
    for manager in managers:
        await zammad.add_watcher(ticket_id, manager["id"])

    log.info(
        "Added %d managers as watchers to ticket %s",
        len(managers),
        ticket_id,
    )
    return {"ok": True, "watchers_added": len(managers)}


# ── Health ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health():
    return {"status": "ok"}
