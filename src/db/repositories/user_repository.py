"""Repository: TelegramUser CRUD."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import TelegramUser


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> TelegramUser | None:
        result = await self._session.execute(
            select(TelegramUser).where(TelegramUser.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_by_zammad_user_id(self, zammad_user_id: int) -> TelegramUser | None:
        result = await self._session.execute(
            select(TelegramUser).where(TelegramUser.zammad_user_id == zammad_user_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        telegram_id: int,
        first_name: str,
        last_name: str | None = None,
        username: str | None = None,
    ) -> TelegramUser:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = TelegramUser(
                telegram_id=telegram_id,
                first_name=first_name,
                last_name=last_name,
                username=username,
            )
            self._session.add(user)
        else:
            user.first_name = first_name
            user.last_name = last_name
            user.username = username
        return user

    async def save_phone(self, telegram_id: int, phone: str) -> None:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.phone = phone

    async def link_zammad_user(self, telegram_id: int, zammad_user_id: int) -> None:
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            user.zammad_user_id = zammad_user_id
