"""
config.py — читает .env и предоставляет типизированный конфиг для setup.py
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(f"Required env variable {key!r} is not set")
    return val


def _opt(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


def _bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


class Config:
    # Zammad
    ZAMMAD_URL: str = _require("ZAMMAD_URL").rstrip("/")
    ZAMMAD_ADMIN_TOKEN: str = _opt("ZAMMAD_ADMIN_TOKEN")
    ZAMMAD_ADMIN_USER: str = _opt("ZAMMAD_ADMIN_USER")
    ZAMMAD_ADMIN_PASS: str = _opt("ZAMMAD_ADMIN_PASS")

    # Telegram
    MANAGER_TELEGRAM_CHAT_ID: str = _opt("MANAGER_TELEGRAM_CHAT_ID")
    TELEGRAM_WEBHOOK_URL: str = _opt("TELEGRAM_WEBHOOK_URL")
    ZAMMAD_WEBHOOK_SECRET: str = _opt("ZAMMAD_WEBHOOK_SECRET", "changeme")

    # Routing
    VIP_ROUTING_MODE: str = _opt("VIP_ROUTING_MODE", "B")  # A or B

    # SLA — Standard
    SLA_STANDARD_P1_FIRST_RESPONSE: int = _int("SLA_STANDARD_P1_FIRST_RESPONSE", 60)
    SLA_STANDARD_P2_FIRST_RESPONSE: int = _int("SLA_STANDARD_P2_FIRST_RESPONSE", 240)
    SLA_STANDARD_P1_SOLUTION: int = _int("SLA_STANDARD_P1_SOLUTION", 480)
    SLA_STANDARD_P2_SOLUTION: int = _int("SLA_STANDARD_P2_SOLUTION", 1440)

    # SLA — VIP
    SLA_VIP_P1_FIRST_RESPONSE: int = _int("SLA_VIP_P1_FIRST_RESPONSE", 15)
    SLA_VIP_P2_FIRST_RESPONSE: int = _int("SLA_VIP_P2_FIRST_RESPONSE", 60)
    SLA_VIP_P1_SOLUTION: int = _int("SLA_VIP_P1_SOLUTION", 120)
    SLA_VIP_P2_SOLUTION: int = _int("SLA_VIP_P2_SOLUTION", 480)

    SLA_ALERT_P2: bool = _bool("SLA_ALERT_P2", True)

    # Calendar
    BUSINESS_HOURS_TIMEZONE: str = _opt("BUSINESS_HOURS_TIMEZONE", "Europe/Moscow")
    BUSINESS_HOURS_START: str = _opt("BUSINESS_HOURS_START", "09:00")
    BUSINESS_HOURS_END: str = _opt("BUSINESS_HOURS_END", "18:00")

    # Internal service URL for Zammad webhooks
    # Should be reachable from Zammad server
    GATEWAY_INTERNAL_URL: str = _opt(
        "GATEWAY_INTERNAL_URL", "http://telegram-gateway:8080"
    )

    @classmethod
    def get_auth_headers(cls) -> dict:
        if cls.ZAMMAD_ADMIN_TOKEN:
            return {"Authorization": f"Token token={cls.ZAMMAD_ADMIN_TOKEN}"}
        elif cls.ZAMMAD_ADMIN_USER and cls.ZAMMAD_ADMIN_PASS:
            import base64
            creds = base64.b64encode(
                f"{cls.ZAMMAD_ADMIN_USER}:{cls.ZAMMAD_ADMIN_PASS}".encode()
            ).decode()
            return {"Authorization": f"Basic {creds}"}
        else:
            raise EnvironmentError(
                "Either ZAMMAD_ADMIN_TOKEN or ZAMMAD_ADMIN_USER+ZAMMAD_ADMIN_PASS must be set"
            )
