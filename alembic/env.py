"""Alembic environment. Runs migrations against a synchronous psycopg connection (the same
`postgresql+psycopg://` URL the app uses async) and reads it from rs_core settings, never
from alembic.ini. GeoAlchemy2's alembic helpers teach autogenerate to render Geometry columns
and skip the PostGIS-managed spatial-index/system tables."""

from __future__ import annotations

import time
from logging.config import fileConfig

import rs_core.models  # noqa: F401  - import registers every table on Base.metadata
from geoalchemy2 import alembic_helpers
from rs_core.config import get_settings
from rs_core.db import Base
from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
_DB_URL = get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=alembic_helpers.include_object,
        render_item=alembic_helpers.render_item,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _connect_with_retry(connectable, *, attempts: int = 30, delay: float = 2.0):
    """A fresh-volume postgres accepts local-socket connections (which satisfy the compose
    healthcheck) before its TCP listener is up, so the migrate service - started the instant
    postgres reports healthy - can hit a transient connection refusal on a cold `up`. Retry the
    first connect briefly so the migration step does not fail stack startup."""
    from sqlalchemy.exc import OperationalError

    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            return connectable.connect()
        except OperationalError as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(
        f"database not reachable for migrations after {attempts} attempts"
    ) from last_error


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _DB_URL
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with _connect_with_retry(connectable) as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=alembic_helpers.include_object,
            render_item=alembic_helpers.render_item,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
