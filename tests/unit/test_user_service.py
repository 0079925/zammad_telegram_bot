"""Unit tests for UserService."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.user_service import UserService
from src.zammad.schemas import ZammadUserSchema


@pytest.fixture
def service(mock_zammad_client) -> UserService:
    return UserService(mock_zammad_client)


# ── ensure_user ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_user_creates_new(service, db_telegram_user):
    """ensure_user should upsert without raising on a fresh user."""
    mock_repo = MagicMock()
    mock_repo.upsert = AsyncMock(return_value=db_telegram_user)

    with patch("src.services.user_service.get_session") as mock_ctx, \
         patch("src.services.user_service.UserRepository", return_value=mock_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        await service.ensure_user(
            telegram_id=123456789,
            first_name="Иван",
            last_name="Петров",
            username="ivan_petrov",
        )

    mock_repo.upsert.assert_called_once_with(
        telegram_id=123456789,
        first_name="Иван",
        last_name="Петров",
        username="ivan_petrov",
    )


# ── register_phone ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_phone_creates_zammad_user(service, mock_zammad_client, zammad_user):
    """When no Zammad user exists, register_phone should create one."""
    mock_zammad_client.search_user_by_login = AsyncMock(return_value=None)
    mock_zammad_client.search_user_by_phone = AsyncMock(return_value=None)
    mock_zammad_client.create_user = AsyncMock(return_value=zammad_user)

    mock_user_repo = MagicMock()
    mock_user_repo.save_phone = AsyncMock()
    mock_user_repo.link_zammad_user = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    with patch("src.services.user_service.get_session") as mock_ctx, \
         patch("src.services.user_service.UserRepository", return_value=mock_user_repo), \
         patch("src.services.user_service.IdempotencyRepository", return_value=mock_idem_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        zammad_id = await service.register_phone(
            telegram_id=123456789,
            phone="+79001234567",
            first_name="Иван",
        )

    assert zammad_id == zammad_user.id
    mock_zammad_client.create_user.assert_called_once()
    mock_user_repo.link_zammad_user.assert_called_once_with(123456789, zammad_user.id)


@pytest.mark.asyncio
async def test_register_phone_finds_existing_zammad_user(service, mock_zammad_client, zammad_user):
    """When Zammad user already exists by login, it should be linked without creating a new one."""
    mock_zammad_client.search_user_by_login = AsyncMock(return_value=zammad_user)

    mock_user_repo = MagicMock()
    mock_user_repo.save_phone = AsyncMock()
    mock_user_repo.link_zammad_user = AsyncMock()

    mock_idem_repo = MagicMock()
    mock_idem_repo.write_log = AsyncMock()

    with patch("src.services.user_service.get_session") as mock_ctx, \
         patch("src.services.user_service.UserRepository", return_value=mock_user_repo), \
         patch("src.services.user_service.IdempotencyRepository", return_value=mock_idem_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        zammad_id = await service.register_phone(
            telegram_id=123456789,
            phone="+79001234567",
            first_name="Иван",
        )

    assert zammad_id == zammad_user.id
    mock_zammad_client.create_user.assert_not_called()


# ── has_phone ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_has_phone_returns_true_when_phone_exists(service, db_telegram_user):
    mock_repo = MagicMock()
    mock_repo.get_by_telegram_id = AsyncMock(return_value=db_telegram_user)

    with patch("src.services.user_service.get_session") as mock_ctx, \
         patch("src.services.user_service.UserRepository", return_value=mock_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        result = await service.has_phone(123456789)

    assert result is True


@pytest.mark.asyncio
async def test_has_phone_returns_false_when_no_user(service):
    mock_repo = MagicMock()
    mock_repo.get_by_telegram_id = AsyncMock(return_value=None)

    with patch("src.services.user_service.get_session") as mock_ctx, \
         patch("src.services.user_service.UserRepository", return_value=mock_repo):
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.return_value = session_mock

        result = await service.has_phone(999)

    assert result is False
