"""
Asynchronous SQLite Database Engine and Session Management.
Configures Write-Ahead Logging (WAL) and PRAGMA settings for Toolforge NFS/local storage.
"""

from collections.abc import AsyncGenerator
import os
from typing import Any, Optional

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.db.models import Base

_engine: Optional[AsyncEngine] = None
_session_maker: Optional[async_sessionmaker[AsyncSession]] = None


def get_engine() -> AsyncEngine:
    """Return the global async SQLAlchemy engine, initializing if necessary."""
    global _engine, _session_maker
    if _engine is None:
        settings = get_settings()

        # Ensure database directory exists
        db_dir = os.path.dirname(os.path.abspath(settings.DB_PATH))
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        engine_kwargs: dict[str, Any] = {
            "echo": settings.DEBUG and settings.ENVIRONMENT == "development",
            "future": True,
            "connect_args": {"timeout": 30.0},
        }
        if ":memory:" not in settings.database_url:
            engine_kwargs.update(
                {
                    "pool_size": 20,
                    "max_overflow": 30,
                    "pool_timeout": 60.0,
                }
            )

        _engine = create_async_engine(
            settings.database_url,
            **engine_kwargs,
        )

        # Set SQLite PRAGMAs for concurrency and durability
        @event.listens_for(_engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL;")
            cursor.execute("PRAGMA synchronous=NORMAL;")
            cursor.execute("PRAGMA busy_timeout=30000;")
            cursor.execute("PRAGMA foreign_keys=ON;")
            cursor.close()

        _session_maker = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )

    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """Return the configured async sessionmaker."""
    if _session_maker is None:
        get_engine()
    assert _session_maker is not None
    return _session_maker


async def init_db() -> None:
    """Initialize database tables according to declarative models."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Cleanly close and dispose of the database engine."""
    global _engine, _session_maker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_maker = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for obtaining an isolated async database session."""
    session_factory = get_session_maker()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


__all__ = [
    "get_engine",
    "get_session_maker",
    "init_db",
    "close_db",
    "get_db",
]
