"""
Integration tests for the Zammad → Telegram webhook flow.

Uses FastAPI TestClient (no real network calls).
The aiogram Bot and Zammad client are mocked.
"""
from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.webhook.app import create_webhook_app


WEBHOOK_SECRET = "test_webhook_secret_here"
WEBHOOK_PATH = "/webhook/zammad"


@pytest.fixture
def webhook_app():
    import os
    os.environ.update({
        "TELEGRAM_BOT_TOKEN": "fake:token",
        "ZAMMAD_URL": "https://zammad.example.com",
        "ZAMMAD_HTTP_TOKEN": "faketoken",
        "ZAMMAD_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "ZAMMAD_INTEGRATION_USER_ID": "5",
        "DATABASE_URL": "postgresql+asyncpg://u:p@localhost/db",
        "ZAMMAD_WEBHOOK_PATH": WEBHOOK_PATH,
    })

    from src.config import Settings
    settings = Settings(
        telegram_bot_token="fake:token",
        zammad_url="https://zammad.example.com",
        zammad_http_token="faketoken",
        zammad_webhook_secret=WEBHOOK_SECRET,
        zammad_integration_user_id=5,
        database_url="postgresql+asyncpg://u:p@localhost/db",
        zammad_webhook_path=WEBHOOK_PATH,
    )

    app = create_webhook_app(settings)

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    mock_bot.send_photo = AsyncMock()
    mock_bot.send_document = AsyncMock()

    mock_zammad = MagicMock()
    mock_zammad.download_attachment = AsyncMock(return_value=b"filedata")

    mock_notification = MagicMock()
    mock_notification.handle_webhook = AsyncMock()

    app.state.notification_service = mock_notification
    return app, mock_notification


@pytest.fixture
def client(webhook_app):
    app, _ = webhook_app
    return TestClient(app), webhook_app[1]


def _auth_header():
    return {"Authorization": f"Bearer {WEBHOOK_SECRET}"}


# ── Health endpoints ──────────────────────────────────────────────────────────

def test_healthz(webhook_app):
    app, _ = webhook_app
    c = TestClient(app)
    resp = c.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz(webhook_app):
    app, _ = webhook_app
    c = TestClient(app)
    resp = c.get("/readyz")
    assert resp.status_code == 200


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_webhook_rejects_missing_auth(webhook_app):
    app, _ = webhook_app
    c = TestClient(app)
    payload = {"ticket": {"id": 1, "number": "1", "title": "T"}, "article": None}
    resp = c.post(WEBHOOK_PATH, json=payload)
    assert resp.status_code == 401


def test_webhook_rejects_wrong_secret(webhook_app):
    app, _ = webhook_app
    c = TestClient(app)
    payload = {"ticket": {"id": 1, "number": "1", "title": "T"}, "article": None}
    resp = c.post(WEBHOOK_PATH, json=payload, headers={"Authorization": "Bearer wrongsecret"})
    assert resp.status_code == 401


# ── Happy path ────────────────────────────────────────────────────────────────

def test_webhook_accepts_valid_payload(webhook_app):
    app, mock_notification = webhook_app
    c = TestClient(app)
    payload = {
        "ticket": {"id": 100, "number": "100001", "title": "Test ticket", "state": {"name": "open"}},
        "article": {
            "id": 200,
            "ticket_id": 100,
            "body": "Ответ агента",
            "internal": False,
            "created_by_id": 99,
            "content_type": "text/plain",
            "attachments": [],
        },
    }
    resp = c.post(WEBHOOK_PATH, json=payload, headers=_auth_header())
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    mock_notification.handle_webhook.assert_called_once()


def test_webhook_returns_ok_on_invalid_payload(webhook_app):
    """Should return 422 (not 500) for malformed payload."""
    app, _ = webhook_app
    c = TestClient(app)
    resp = c.post(WEBHOOK_PATH, json={"bad": "data"}, headers=_auth_header())
    assert resp.status_code == 422


# ── Anti-loop logic ───────────────────────────────────────────────────────────

def test_webhook_calls_handle_webhook_exactly_once(webhook_app):
    """Ensure no double-dispatch on a single webhook call."""
    app, mock_notification = webhook_app
    c = TestClient(app)
    payload = {
        "ticket": {"id": 100, "number": "100001", "title": "T", "state": {"name": "open"}},
        "article": {
            "id": 201,
            "ticket_id": 100,
            "body": "Hello",
            "internal": False,
            "created_by_id": 10,
            "content_type": "text/plain",
            "attachments": [],
        },
    }
    c.post(WEBHOOK_PATH, json=payload, headers=_auth_header())
    assert mock_notification.handle_webhook.call_count == 1
