"""
Application configuration.

All settings are loaded from environment variables (or a .env file in dev).
Docker secrets can be mounted under SECRETS_DIR (default: /run/secrets).
No values are hard-coded here; everything must be supplied externally.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Docker secrets directory (e.g. /run/secrets/telegram_bot_token)
        secrets_dir=os.getenv("SECRETS_DIR", None),
    )

    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: SecretStr
    telegram_webhook_url: str | None = None
    telegram_webhook_secret: SecretStr | None = None

    # ── Zammad ────────────────────────────────────────────────────────────────
    zammad_url: str
    zammad_http_token: SecretStr
    zammad_webhook_secret: SecretStr
    zammad_integration_user_id: int
    zammad_group_support: str = "Support L1"
    zammad_group_manager: str = "manager"

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str  # postgresql+asyncpg://...

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"

    # ── App ───────────────────────────────────────────────────────────────────
    environment: Literal["development", "production", "testing"] = "production"
    log_level: str = "INFO"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    zammad_webhook_path: str = "/webhook/zammad"

    # ── Attachments ───────────────────────────────────────────────────────────
    max_attachment_size_bytes: int = 20 * 1024 * 1024  # 20 MB
    allowed_content_types: list[str] = [
        "image/jpeg",
        "image/png",
        "image/gif",
        "image/webp",
        "application/pdf",
        "text/plain",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "video/mp4",
        "audio/mpeg",
        "audio/ogg",
    ]

    # ── Zammad client ─────────────────────────────────────────────────────────
    zammad_request_timeout: float = 30.0
    zammad_max_retries: int = 3
    zammad_retry_wait_seconds: float = 1.0

    @field_validator("zammad_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
