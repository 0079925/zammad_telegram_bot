"""Unit tests for ZammadClient — HTTP layer."""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from src.zammad.client import ZammadAPIError, ZammadClient, _sanitize_filename
from src.zammad.schemas import ZammadUserSchema


# ── Filename sanitization ─────────────────────────────────────────────────────

@pytest.mark.parametrize("input_name,safe", [
    ("report.pdf", "report.pdf"),
    ("../../../etc/passwd", "passwd"),          # path traversal stripped
    ("my file (1).docx", "my_file__1_.docx"),  # spaces/parens replaced
    ("a" * 300 + ".txt", "a" * 200),           # truncated at 200 chars
    ("", "file"),                               # empty fallback
])
def test_sanitize_filename(input_name, safe):
    result = _sanitize_filename(input_name)
    assert result == safe
    assert len(result) <= 200
    assert ".." not in result


# ── API error handling ────────────────────────────────────────────────────────

@pytest.mark.asyncio
@respx.mock
async def test_raises_on_4xx():
    respx.get("https://zammad.example.com/api/v1/users/search").mock(
        return_value=Response(404, json={"error": "not found"})
    )

    import os
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "fake:token")
    os.environ.setdefault("ZAMMAD_URL", "https://zammad.example.com")
    os.environ.setdefault("ZAMMAD_HTTP_TOKEN", "faketoken")
    os.environ.setdefault("ZAMMAD_WEBHOOK_SECRET", "fakesecret")
    os.environ.setdefault("ZAMMAD_INTEGRATION_USER_ID", "1")
    os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")

    from src.config import Settings
    settings = Settings(
        telegram_bot_token="fake:token",
        zammad_url="https://zammad.example.com",
        zammad_http_token="faketoken",
        zammad_webhook_secret="fakesecret",
        zammad_integration_user_id=1,
        database_url="postgresql+asyncpg://u:p@localhost/db",
    )

    async with ZammadClient(settings) as client:
        with pytest.raises(ZammadAPIError) as exc_info:
            await client.search_user_by_login("tg_123")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
@respx.mock
async def test_search_user_returns_none_on_empty_list():
    respx.get("https://zammad.example.com/api/v1/users/search").mock(
        return_value=Response(200, json=[])
    )

    from src.config import Settings
    settings = Settings(
        telegram_bot_token="fake:token",
        zammad_url="https://zammad.example.com",
        zammad_http_token="faketoken",
        zammad_webhook_secret="fakesecret",
        zammad_integration_user_id=1,
        database_url="postgresql+asyncpg://u:p@localhost/db",
    )

    async with ZammadClient(settings) as client:
        result = await client.search_user_by_login("tg_999")

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_search_user_returns_schema_on_hit():
    user_data = {
        "id": 7,
        "login": "tg_123",
        "email": "tg_123@telegram.bot",
        "firstname": "Test",
        "lastname": "User",
        "phone": "+79001112233",
    }
    respx.get("https://zammad.example.com/api/v1/users/search").mock(
        return_value=Response(200, json=[user_data])
    )

    from src.config import Settings
    settings = Settings(
        telegram_bot_token="fake:token",
        zammad_url="https://zammad.example.com",
        zammad_http_token="faketoken",
        zammad_webhook_secret="fakesecret",
        zammad_integration_user_id=1,
        database_url="postgresql+asyncpg://u:p@localhost/db",
    )

    async with ZammadClient(settings) as client:
        result = await client.search_user_by_login("tg_123")

    assert isinstance(result, ZammadUserSchema)
    assert result.id == 7
    assert result.login == "tg_123"
