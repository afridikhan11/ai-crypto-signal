"""
Alembic environment.

Reads the database URL from `app.core.config` - the SAME source
`app/core/database.py` uses to build the application engine - so a
migration can never be applied to a different database than the one the
app talks to.

The application uses an ASYNC driver (`postgresql+asyncpg://`), so the
online path runs through `AsyncEngine` + `connection.run_sync()`, which is
Alembic's documented pattern for async drivers.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import get_settings

# Import every model module so `Base.metadata` is fully populated before
# autogenerate compares it against the live schema. Importing the Base
# alone would leave the tables unregistered.
from app.models.base import Base
from app.models import coin as _coin  # noqa: F401
from app.models import signal as _signal  # noqa: F401
from app.models import equity_snapshot as _equity_snapshot  # noqa: F401
from app.models import risk_assessment as _risk_assessment  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (`--sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
