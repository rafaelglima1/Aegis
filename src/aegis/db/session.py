"""AEGIS database session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import create_engine as sa_create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from aegis.config import get_settings


def create_engine(url: str | None = None, echo: bool = False) -> Any:
    """Create a synchronous SQLAlchemy engine."""
    if url is None:
        settings = get_settings()
        url = settings.database_url
    return sa_create_engine(url.replace("+asyncpg", ""), echo=echo)


def create_async_engine_from_settings(url: str | None = None, echo: bool = False) -> Any:
    """Create an async SQLAlchemy engine."""
    if url is None:
        settings = get_settings()
        url = settings.database_url
    return create_async_engine(url, echo=echo)


def get_session_factory(engine: Any) -> sessionmaker:
    """Create a synchronous session factory."""
    return sessionmaker(bind=engine)


def get_async_session_factory(engine: Any) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory."""
    return async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def get_db_session(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Get an async database session."""
    if session_factory is None:
        engine = create_async_engine_from_settings()
        session_factory = get_async_session_factory(engine)

    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
