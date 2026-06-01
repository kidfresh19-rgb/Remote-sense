"""Async database access. One engine + sessionmaker per process, created lazily from
config so importing the models never opens a connection (keeps rs_imagery / rs_analysis
testable with zero DB). PostGIS lives behind GeoAlchemy2; the canonical join key across the
split-ownership boundary is the gateway farm id (CLAUDE.md invariant 6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from rs_core.config import Settings, get_settings


class Base(DeclarativeBase):
    """Declarative base for every ORM model in rs_core.models."""


def _async_url(database_url: str) -> str:
    """psycopg3 drives both sync (Alembic) and async (the app) from one URL scheme.
    `postgresql+psycopg://` is already async-capable, so we pass it through unchanged."""
    return database_url


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        _async_url(settings.database_url),
        pool_pre_ping=True,
        future=True,
    )


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: a session scoped to one request, committed on success and
    rolled back on error."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def engine_for(settings: Settings) -> AsyncEngine:
    """Build a one-off engine for an explicit Settings (tests, scripts). Not cached."""
    return create_async_engine(_async_url(settings.database_url), future=True)
