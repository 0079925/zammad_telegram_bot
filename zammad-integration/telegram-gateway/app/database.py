"""
database.py — SQLAlchemy async engine + session factory.
Uses SQLite (aiosqlite driver) — no extra services, survives restarts,
atomic writes, sufficient for thousands of chats.
"""

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# aiosqlite requires sqlite+aiosqlite:///
_db_url = settings.database_url
if _db_url.startswith("sqlite:///") and "aiosqlite" not in _db_url:
    _db_url = _db_url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)

engine = create_async_engine(
    _db_url,
    connect_args={"check_same_thread": False},
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Create tables on startup."""
    async with engine.begin() as conn:
        from app import models  # noqa: F401 — registers models
        await conn.run_sync(Base.metadata.create_all)
