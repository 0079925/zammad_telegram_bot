#!/usr/bin/env python3
"""
setup.py — полная настройка Zammad через REST API.

Запуск:
  pip install -r requirements.txt
  cp ../.env.example ../.env   # заполни значения
  python setup.py

Идемпотентен: повторный запуск не создаёт дубликатов.
"""

import sys
import json
import logging
from typing import Optional

import requests

from config import Config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# HTTP helpers
# ──────────────────────────────────────────────────────────────────────────────

SESSION = requests.Session()
SESSION.headers.update(
    {**Config.get_auth_headers(), "Content-Type": "application/json"}
)

BASE = Config.ZAMMAD_URL


def get(path: str) -> list | dict:
    r = SESSION.get(f"{BASE}{path}")
    r.raise_for_status()
    return r.json()


def post(path: str, payload: dict) -> dict:
    r = SESSION.post(f"{BASE}{path}", json=payload)
    if not r.ok:
        log.error("POST %s → %s %s", path, r.status_code, r.text)
        r.raise_for_status()
    return r.json()


def put(path: str, payload: dict) -> dict:
    r = SESSION.put(f"{BASE}{path}", json=payload)
    if not r.ok:
        log.error("PUT %s → %s %s", path, r.status_code, r.text)
        r.raise_for_status()
    return r.json()


def find_by_name(items: list, name: str, field: str = "name") -> Optional[dict]:
    return next((i for i in items if i.get(field) == name), None)


# ──────────────────────────────────────────────────────────────────────────────
# 1. GROUPS
# ──────────────────────────────────────────────────────────────────────────────

GROUP_NAMES = ["Support L1", "Support L2", "Support L3", "Managers"]

if Config.VIP_ROUTING_MODE == "A":
    GROUP_NAMES.append("VIP L1")


def ensure_group(name: str) -> dict:
    existing = get("/api/v1/groups")
    found = find_by_name(existing, name)
    if found:
        log.info("Group '%s' already exists (id=%s)", name, found["id"])
        return found
    result = post("/api/v1/groups", {"name": name, "active": True})
    log.info("Created group '%s' (id=%s)", name, result["id"])
    return result


def setup_groups() -> dict[str, int]:
    log.info("=== Setting up Groups ===")
    groups = {}
    for name in GROUP_NAMES:
        g = ensure_group(name)
        groups[name] = g["id"]
    return groups


# ──────────────────────────────────────────────────────────────────────────────
# 2. ROLES
# ──────────────────────────────────────────────────────────────────────────────
# In Zammad, roles define system-level permissions (ticket.agent, admin.*, etc.)
# Group-level access (full/read) is assigned per USER, not per Role.
# We create roles with correct permissions; group membership instructions
# are in docs/UI_STEPS.md.
# ──────────────────────────────────────────────────────────────────────────────

ROLE_DEFINITIONS = [
    {
        "name": "agent_l1",
        "note": "L1 support agent",
        "permissions": ["ticket.agent"],
        # group_access: key = group name, value = access level
        # This is applied to the Role object if Zammad supports it,
        # otherwise it's documented in UI_STEPS.md
        "group_access": {
            "Support L1": "full",
            "Support L2": "read",
            "Support L3": "read",
        },
    },
    {
        "name": "agent_l2",
        "note": "L2 support agent",
        "permissions": ["ticket.agent"],
        "group_access": {
            "Support L1": "read",
            "Support L2": "full",
            "Support L3": "read",
        },
    },
    {
        "name": "agent_l3",
        "note": "L3 support agent",
        "permissions": ["ticket.agent"],
        "group_access": {
            "Support L1": "read",
            "Support L2": "read",
            "Support L3": "full",
        },
    },
    {
        "name": "manager",
        "note": "Team manager — full access to Managers group, read-only to Support",
        "permissions": ["ticket.agent"],
        "group_access": {
            "Managers": "full",
            # Option A: read access to all Support groups
            "Support L1": "read",
            "Support L2": "read",
            "Support L3": "read",
            # Option B: manager only sees tickets where they are owner/watcher
            # → Configure in Admin > Roles > manager > group_ids with "change" level
            #   and set default_at to "overview" so they see their own tickets.
            # Both options documented in docs/UI_STEPS.md
        },
    },
]


def ensure_role(definition: dict, groups: dict[str, int]) -> dict:
    existing_roles = get("/api/v1/roles")
    found = find_by_name(existing_roles, definition["name"])

    # Build group_ids payload {group_id: [access_level]}
    group_ids = {}
    for gname, access in definition["group_access"].items():
        gid = groups.get(gname)
        if gid:
            group_ids[str(gid)] = [access]
        else:
            log.warning("Group '%s' not found, skipping group_ids entry", gname)

    payload = {
        "name": definition["name"],
        "note": definition["note"],
        "active": True,
        "group_ids": group_ids,
    }

    if found:
        log.info("Role '%s' exists (id=%s), updating...", definition["name"], found["id"])
        result = put(f"/api/v1/roles/{found['id']}", payload)
        return result

    result = post("/api/v1/roles", payload)
    log.info("Created role '%s' (id=%s)", definition["name"], result["id"])
    return result


def setup_roles(groups: dict[str, int]) -> dict[str, int]:
    log.info("=== Setting up Roles ===")
    roles = {}
    for rd in ROLE_DEFINITIONS:
        r = ensure_role(rd, groups)
        roles[rd["name"]] = r["id"]
    return roles


# ──────────────────────────────────────────────────────────────────────────────
# 3. CUSTOM FIELD: customer_type
# ──────────────────────────────────────────────────────────────────────────────

def setup_customer_type_field():
    log.info("=== Setting up custom field: customer_type ===")

    existing = get("/api/v1/object_manager_attributes?object=Ticket")
    if isinstance(existing, list):
        if any(a.get("name") == "customer_type" for a in existing):
            log.info("Field 'customer_type' already exists, skipping")
            return

    payload = {
        "object": "Ticket",
        "name": "customer_type",
        "display": "Customer Type",
        "data_type": "select",
        "data_option": {
            "options": {
                "standard": "Standard",
                "vip": "VIP",
                "enterprise": "Enterprise",
            },
            "default": "standard",
            "null": True,
            "translate": True,
        },
        "active": True,
        "screens": {
            "create_middle": {"shown": True, "required": False},
            "edit": {"shown": True, "required": False},
            "view": {"shown": True},
        },
        "position": 100,
    }

    result = post("/api/v1/object_manager_attributes", payload)
    log.info("Created field 'customer_type' (id=%s)", result.get("id"))

    # Migrate DB schema
    log.info("Executing DB migration for new field...")
    try:
        post("/api/v1/object_manager_attributes/execute_migrations", {})
        log.info("Migration done")
    except Exception as e:
        log.warning("Migration endpoint error (may need manual execution in UI): %s", e)


# ──────────────────────────────────────────────────────────────────────────────
# 4. CALENDAR
# ──────────────────────────────────────────────────────────────────────────────

def setup_calendar() -> int:
    log.info("=== Setting up Calendar ===")
    name = "Business 5/2"
    existing = get("/api/v1/calendars")
    found = find_by_name(existing, name)
    if found:
        log.info("Calendar '%s' already exists (id=%s)", name, found["id"])
        return found["id"]

    start = Config.BUSINESS_HOURS_START
    end = Config.BUSINESS_HOURS_END

    day_hours = {"active": True, "timerange": [{"from": start, "to": end}]}
    off_day = {"active": False, "timerange": [{"from": "00:00", "to": "00:00"}]}

    payload = {
        "name": name,
        "timezone": Config.BUSINESS_HOURS_TIMEZONE,
        "business_hours": {
            "mon": day_hours,
            "tue": day_hours,
            "wed": day_hours,
            "thu": day_hours,
            "fri": day_hours,
            "sat": off_day,
            "sun": off_day,
        },
        "default": False,
        "active": True,
        "public_holidays": {},
    }

    result = post("/api/v1/calendars", payload)
    log.info("Created calendar '%s' (id=%s)", name, result["id"])
    return result["id"]


# ──────────────────────────────────────────────────────────────────────────────
# 5. SLA POLICIES
# ──────────────────────────────────────────────────────────────────────────────

def setup_slas(calendar_id: int) -> dict[str, int]:
    log.info("=== Setting up SLA Policies ===")

    sla_defs = [
        {
            "name": "SLA Standard",
            "first_response_time": Config.SLA_STANDARD_P1_FIRST_RESPONSE,
            "solution_time": Config.SLA_STANDARD_P1_SOLUTION,
            # condition: customer_type = standard (or null/empty → default)
            "condition": {
                "ticket.customer_type": {
                    "operator": "is",
                    "value": ["standard", ""],
                }
            },
        },
        {
            "name": "SLA VIP",
            "first_response_time": Config.SLA_VIP_P1_FIRST_RESPONSE,
            "solution_time": Config.SLA_VIP_P1_SOLUTION,
            # condition: customer_type = vip OR enterprise
            "condition": {
                "ticket.customer_type": {
                    "operator": "is",
                    "value": ["vip", "enterprise"],
                }
            },
        },
    ]

    existing = get("/api/v1/slas")
    sla_ids = {}

    for sla in sla_defs:
        found = find_by_name(existing, sla["name"])
        payload = {
            "name": sla["name"],
            "condition": sla["condition"],
            "first_response_time": sla["first_response_time"],
            "solution_time": sla["solution_time"],
            "calendar_id": calendar_id,
            "active": True,
        }
        if found:
            log.info("SLA '%s' exists (id=%s), updating...", sla["name"], found["id"])
            result = put(f"/api/v1/slas/{found['id']}", payload)
        else:
            result = post("/api/v1/slas", payload)
            log.info("Created SLA '%s' (id=%s)", sla["name"], result["id"])
        sla_ids[sla["name"]] = result["id"]

    return sla_ids


# ──────────────────────────────────────────────────────────────────────────────
# 6. TRIGGERS
# ──────────────────────────────────────────────────────────────────────────────

def get_priority_id(name: str) -> Optional[int]:
    priorities = get("/api/v1/ticket_priorities")
    found = find_by_name(priorities, name)
    return found["id"] if found else None


def get_group_id(name: str, groups: dict[str, int]) -> Optional[int]:
    return groups.get(name)


def setup_triggers(groups: dict[str, int]):
    log.info("=== Setting up Triggers ===")

    gateway_url = Config.GATEWAY_INTERNAL_URL
    webhook_secret = Config.ZAMMAD_WEBHOOK_SECRET

    l1_id = str(groups.get("Support L1", ""))
    managers_id = str(groups.get("Managers", ""))
    vip_group_id = str(groups.get("VIP L1", "")) if Config.VIP_ROUTING_MODE == "A" else l1_id

    prio_normal = get_priority_id("2 normal")
    prio_high = get_priority_id("1 high")

    trigger_defs = [
        # ── T1: New ticket → default to Support L1 ────────────────────────────
        {
            "name": "[Route] New ticket → Support L1",
            "condition": {
                "ticket.action": {"operator": "is", "value": "create"},
                "ticket.group_id": {"operator": "is not set"},
            },
            "perform": {
                "ticket.group_id": {"value": l1_id},
            },
            "active": True,
        },
        # ── T2: VIP/Enterprise routing ─────────────────────────────────────────
        # Variant B: same L1 group, priority HIGH
        # Variant A: route to VIP L1 group
        {
            "name": f"[Route] VIP/Enterprise → {'VIP L1' if Config.VIP_ROUTING_MODE == 'A' else 'L1 High Priority'}",
            "condition": {
                "ticket.action": {"operator": "is", "value": "create"},
                "ticket.customer_type": {
                    "operator": "is",
                    "value": ["vip", "enterprise"],
                },
            },
            "perform": {
                "ticket.group_id": {"value": vip_group_id},
                **({"ticket.priority_id": {"value": str(prio_high)}} if Config.VIP_ROUTING_MODE == "B" and prio_high else {}),
            },
            "active": True,
        },
        # ── T3: Commercial/Billing topics → Managers ──────────────────────────
        {
            "name": "[Route] Billing/Equipment → Managers",
            "condition": {
                "ticket.action": {"operator": "is", "value": "create"},
                "ticket.title": {
                    "operator": "contains one of",
                    "value": ["счёт", "счет", "оборудование", "коммерция", "invoice", "billing", "equipment"],
                },
            },
            "perform": {
                "ticket.group_id": {"value": managers_id},
                # NOTE: "Add watcher" is NOT available as a native trigger action in Zammad.
                # The gateway's /webhook/zammad/new_ticket endpoint handles this via API.
                # See docs/UI_STEPS.md for manual alternative.
            },
            "active": True,
        },
        # ── T4: SLA Breach → webhook → Telegram alert ─────────────────────────
        {
            "name": "[SLA] P1 breach → notify manager",
            "condition": {
                "ticket.escalation_at": {"operator": "is not set"},  # escalated
                "ticket.priority_id": {
                    "operator": "is",
                    "value": [str(prio_high)] if prio_high else [],
                },
            },
            "perform": {
                # Zammad can send HTTP notifications via "notification.http"
                # This requires Zammad >= 5.x with HTTP notifications enabled
                "notification.http": {
                    "recipient": "http",
                    "url": f"{gateway_url}/webhook/zammad/escalation",
                    "method": "POST",
                    "headers": [
                        {"name": "X-Zammad-Webhook-Secret", "value": webhook_secret}
                    ],
                    "body": '{"ticket_id": "#{ticket.id}", "priority": "#{ticket.priority}", "sla": "#{ticket.sla_calculated_at}"}',
                },
            },
            "active": True,
        },
        # ── T5: SLA Breach → email to customer ────────────────────────────────
        {
            "name": "[SLA] Breach → email customer",
            "condition": {
                "ticket.escalation_at": {"operator": "is not set"},
            },
            "perform": {
                "notification.email": {
                    "recipient": "customer",
                    "subject": "Уведомление: задержка ответа по вашей заявке ##{ticket.number}",
                    "body": (
                        "Здравствуйте, #{ticket.customer.firstname}!\n\n"
                        "Мы приносим извинения за задержку ответа по заявке ##{ticket.number}: "
                        "#{ticket.title}\n\n"
                        "Наши специалисты работают над решением вашего вопроса. "
                        "Мы свяжемся с вами в ближайшее время.\n\n"
                        "С уважением,\nСлужба поддержки"
                    ),
                }
            },
            "active": True,
        },
        # ── T6: Agent public reply → webhook → Telegram ───────────────────────
        {
            "name": "[Telegram] Agent reply → send to customer",
            "condition": {
                "ticket.action": {"operator": "is", "value": "update"},
                "article.type": {"operator": "is", "value": "note"},
                "article.sender": {"operator": "is", "value": "Agent"},
                "article.internal": {"operator": "is", "value": "false"},
            },
            "perform": {
                "notification.http": {
                    "recipient": "http",
                    "url": f"{gateway_url}/webhook/zammad/article",
                    "method": "POST",
                    "headers": [
                        {"name": "X-Zammad-Webhook-Secret", "value": webhook_secret}
                    ],
                    "body": (
                        '{"ticket_id": "#{ticket.id}", '
                        '"article_body": "#{article.body}", '
                        '"ticket_number": "#{ticket.number}"}'
                    ),
                }
            },
            "active": True,
        },
    ]

    existing_triggers = get("/api/v1/triggers")

    for td in trigger_defs:
        found = find_by_name(existing_triggers, td["name"])
        if found:
            log.info("Trigger '%s' exists (id=%s), updating...", td["name"], found["id"])
            put(f"/api/v1/triggers/{found['id']}", td)
        else:
            result = post("/api/v1/triggers", td)
            log.info("Created trigger '%s' (id=%s)", td["name"], result["id"])


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    log.info("Starting Zammad setup...")
    log.info("Target: %s", Config.ZAMMAD_URL)
    log.info("VIP routing mode: %s", Config.VIP_ROUTING_MODE)

    # Sanity check
    try:
        info = get("/api/v1/users/me")
        log.info("Authenticated as: %s", info.get("login"))
    except Exception as e:
        log.error("Cannot connect to Zammad: %s", e)
        sys.exit(1)

    groups = setup_groups()
    roles = setup_roles(groups)
    setup_customer_type_field()
    calendar_id = setup_calendar()
    sla_ids = setup_slas(calendar_id)
    setup_triggers(groups)

    log.info("=== Setup complete! ===")
    log.info("Groups: %s", groups)
    log.info("Roles:  %s", roles)
    log.info("SLAs:   %s", sla_ids)
    log.info("Calendar id: %s", calendar_id)
    log.info(
        "\n⚠️  Manual steps required — see docs/UI_STEPS.md\n"
        "  - Assign roles to agents in Admin > Users\n"
        "  - Verify DB migration in Admin > System > Object Manager\n"
        "  - Configure SMTP channel in Admin > Channels > Email\n"
    )


if __name__ == "__main__":
    main()
