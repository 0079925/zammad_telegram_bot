"""
UserService — manages Telegram ↔ Zammad user lifecycle.

Responsibilities:
    - Upsert TelegramUser record in our DB
    - Create or find the corresponding Zammad user
    - Link phone number to both records
"""
from __future__ import annotations

import structlog

from src.db.repositories import IdempotencyRepository, UserRepository
from src.db.session import get_session
from src.zammad.client import ZammadClient

logger = structlog.get_logger(__name__)


class UserService:
    def __init__(self, zammad: ZammadClient) -> None:
        self._zammad = zammad

    # ── Public API ────────────────────────────────────────────────────────────

    async def ensure_user(
        self,
        *,
        telegram_id: int,
        first_name: str,
        last_name: str | None = None,
        username: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        """
        Upsert TelegramUser.  Does NOT create a Zammad user yet
        (we need a phone number for that).
        """
        log = logger.bind(telegram_id=telegram_id, correlation_id=correlation_id)
        async with get_session() as session:
            repo = UserRepository(session)
            await repo.upsert(
                telegram_id=telegram_id,
                first_name=first_name,
                last_name=last_name,
                username=username,
            )
        log.debug("telegram_user_upserted")

    async def register_phone(
        self,
        *,
        telegram_id: int,
        phone: str,
        first_name: str,
        last_name: str | None = None,
        correlation_id: str | None = None,
    ) -> int:
        """
        Save phone to TelegramUser and find/create the Zammad user.
        Returns the Zammad user ID.
        """
        log = logger.bind(telegram_id=telegram_id, correlation_id=correlation_id)

        async with get_session() as session:
            user_repo = UserRepository(session)
            idem_repo = IdempotencyRepository(session)

            # Persist phone
            await user_repo.save_phone(telegram_id, phone)

            # Find or create Zammad user
            login = f"tg_{telegram_id}"
            zammad_user = await self._zammad.search_user_by_login(login)

            if zammad_user is None:
                # Try to find by phone
                zammad_user = await self._zammad.search_user_by_phone(phone)

            if zammad_user is None:
                email = f"tg_{telegram_id}@telegram.bot"
                zammad_user = await self._zammad.create_user(
                    login=login,
                    email=email,
                    firstname=first_name,
                    lastname=last_name or "",
                    phone=phone,
                )
                log.info("zammad_user_created", zammad_user_id=zammad_user.id)
                await idem_repo.write_log(
                    event_type="user_created",
                    telegram_id=telegram_id,
                    correlation_id=correlation_id,
                    payload={"zammad_user_id": zammad_user.id},
                )
            else:
                # Update phone if missing
                if not zammad_user.phone:
                    await self._zammad.update_user(zammad_user.id, phone=phone)
                log.info("zammad_user_found", zammad_user_id=zammad_user.id)

            await user_repo.link_zammad_user(telegram_id, zammad_user.id)
            await idem_repo.write_log(
                event_type="phone_saved",
                telegram_id=telegram_id,
                correlation_id=correlation_id,
            )

        return zammad_user.id

    async def get_zammad_user_id(self, telegram_id: int) -> int | None:
        """Return the linked Zammad user ID or None if not registered yet."""
        async with get_session() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(telegram_id)
            return user.zammad_user_id if user else None

    async def has_phone(self, telegram_id: int) -> bool:
        async with get_session() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(telegram_id)
            return bool(user and user.phone)
