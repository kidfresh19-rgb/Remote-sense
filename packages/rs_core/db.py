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


def engine_kwargs(settings: Settings) -> dict[str, float | int | bool]:
    """Pool tuning shared by every app engine (S4.4), read from config so a deployment sizes
    its pools per replica count without a code change. Pure - unit-testable with a bare
    Settings."""
    return {
        "pool_pre_ping": True,
        "future": True,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout_s,
        "pool_recycle": settings.db_pool_recycle_s,
    }


def read_database_url(settings: Settings) -> str | None:
    """The replica DSN for analytical reads, or None when reads belong on the primary: the
    switch is unset (no replica provisioned yet, the S4.4 default) or points at the primary
    itself (no second pool for nothing)."""
    if not settings.database_read_url or settings.database_read_url == settings.database_url:
        return None
    return settings.database_read_url


@lru_cache
def get_engine() -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(_async_url(settings.database_url), **engine_kwargs(settings))


@lru_cache
def get_read_engine() -> AsyncEngine:
    """The engine for analytical reads (S4.4): the replica when one is configured, else the
    primary engine itself - the same object, the same pool, so the default deployment pays
    nothing for the indirection."""
    settings = get_settings()
    url = read_database_url(settings)
    if url is None:
        return get_engine()
    return create_async_engine(_async_url(url), **engine_kwargs(settings))


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_engine(),
        expire_on_commit=False,
        autoflush=False,
    )


@lru_cache
def get_read_sessionmaker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_read_engine(),
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


async def get_read_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for read-only endpoints: a session on the read engine, never
    committed (there is nothing to commit; close rolls the read transaction back). Endpoints
    that need read-after-write stay on `get_session` - a replica may lag the primary."""
    maker = get_read_sessionmaker()
    async with maker() as session:
        yield session


def engine_for(settings: Settings) -> AsyncEngine:
    """Build a one-off engine for an explicit Settings (tests, scripts). Not cached."""
    return create_async_engine(_async_url(settings.database_url), future=True)
