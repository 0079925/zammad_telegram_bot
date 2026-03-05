"""
zammad_client.py — async wrapper around Zammad REST API.
"""

import logging
from typing import Optional

import httpx

from app.config import settings

log = logging.getLogger(__name__)

BASE = settings.zammad_url.rstrip("/")
HEADERS = {**settings.zammad_auth_headers, "Content-Type": "application/json"}


async def _get(path: str) -> dict | list:
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        r = await client.get(f"{BASE}{path}")
        r.raise_for_status()
        return r.json()


async def _post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        r = await client.post(f"{BASE}{path}", json=payload)
        if not r.is_success:
            log.error("POST %s → %s %s", path, r.status_code, r.text)
            r.raise_for_status()
        return r.json()


async def _put(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(headers=HEADERS, timeout=10) as client:
        r = await client.put(f"{BASE}{path}", json=payload)
        r.raise_for_status()
        return r.json()


# ── Customer management ──────────────────────────────────────────────────────

async def find_customer_by_telegram(chat_id: str) -> Optional[dict]:
    """Search for a Zammad user by stored telegram_chat_id note."""
    try:
        results = await _get(f"/api/v1/users/search?query=telegram_{chat_id}&limit=1")
        if isinstance(results, list) and results:
            return results[0]
    except Exception as e:
        log.warning("Customer lookup failed: %s", e)
    return None


async def create_customer(
    chat_id: str,
    first_name: str,
    last_name: str = "",
    username: str = "",
) -> dict:
    """Create a new Zammad customer for a Telegram user."""
    login = username or f"tg_{chat_id}"
    email = f"telegram_{chat_id}@telegram.local"
    payload = {
        "login": login,
        "firstname": first_name,
        "lastname": last_name or "",
        "email": email,
        "note": f"Telegram chat_id: {chat_id}",
        "roles": ["Customer"],
        "active": True,
    }
    result = await _post("/api/v1/users", payload)
    log.info("Created Zammad customer id=%s for Telegram chat_id=%s", result["id"], chat_id)
    return result


async def get_or_create_customer(
    chat_id: str,
    first_name: str,
    last_name: str = "",
    username: str = "",
) -> dict:
    existing = await find_customer_by_telegram(chat_id)
    if existing:
        return existing
    return await create_customer(chat_id, first_name, last_name, username)


# ── Ticket management ────────────────────────────────────────────────────────

async def get_ticket(ticket_id: int) -> dict:
    return await _get(f"/api/v1/tickets/{ticket_id}")


async def create_ticket(
    customer_id: int,
    title: str,
    body: str,
    group_name: str = "Support L1",
) -> dict:
    payload = {
        "title": title,
        "group": group_name,
        "customer_id": customer_id,
        "article": {
            "subject": title,
            "body": body,
            "type": "note",
            "internal": False,
            "sender": "Customer",
        },
        "state": "new",
    }
    result = await _post("/api/v1/tickets", payload)
    log.info("Created ticket id=%s '%s'", result["id"], title)
    return result


async def add_article(ticket_id: int, body: str, internal: bool = False) -> dict:
    """Add a customer message as a new article to an existing ticket."""
    payload = {
        "ticket_id": ticket_id,
        "subject": "Telegram message",
        "body": body,
        "type": "note",
        "internal": internal,
        "sender": "Customer",
    }
    return await _post("/api/v1/ticket_articles", payload)


async def is_ticket_open(ticket_id: int) -> bool:
    """Check if ticket is in an open/non-closed state."""
    try:
        ticket = await get_ticket(ticket_id)
        state_name = ticket.get("state", "")
        closed_states = {"closed", "merged", "removed"}
        return state_name.lower() not in closed_states
    except Exception:
        return False


# ── Manager watcher ──────────────────────────────────────────────────────────

async def get_managers_group_agents() -> list[dict]:
    """Return agent users assigned to the Managers group."""
    try:
        users = await _get("/api/v1/users?role=Agent&expand=true")
        if not isinstance(users, list):
            return []
        managers = [u for u in users if "Managers" in (u.get("group_ids") or {})]
        return managers
    except Exception as e:
        log.warning("Could not fetch managers: %s", e)
        return []


async def add_watcher(ticket_id: int, user_id: int):
    """Subscribe a user as watcher on a ticket."""
    try:
        await _post(f"/api/v1/tickets/{ticket_id}/subscribe", {"user_id": user_id})
        log.info("Added user %s as watcher to ticket %s", user_id, ticket_id)
    except Exception as e:
        log.warning("Could not add watcher: %s", e)
