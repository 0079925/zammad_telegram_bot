"""
Async SQLAlchemy engine + session factory.

Usage:
    async with get_session() as session:
        result = await session.execute(...)
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.config import get_settings

_engine = None
_session_factory = None


def _get_engine():  # type: ignore[return]
    global _engine
    if _engine is None:
        settings = get_settings()
        engine_kwargs: dict = {
            "echo": settings.is_development,
            "pool_pre_ping": True,
        }
        # SQLite does not support pool_size/max_overflow options used by server DBs.
        if not settings.database_url.startswith("sqlite+"):
            engine_kwargs.update({"pool_size": 10, "max_overflow": 20})
        _engine = create_async_engine(
            settings.database_url,
            **engine_kwargs,
        )
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=_get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
