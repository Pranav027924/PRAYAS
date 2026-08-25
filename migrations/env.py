"""Alembic environment.

Runs as the OWNER role (ADR-004). Autogenerate is deliberately unused: none of
what matters in this schema — RLS policies, FORCE, partitioning, roles, grants —
is visible to SQLAlchemy metadata reflection, so every migration here is
hand-written with an explicit ``downgrade()`` (ADR-011).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from prayas.config import owner_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", owner_database_url())

# No declarative models: this project drives DDL directly (ADR-003 chose Core).
target_metadata = None


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Wrap each migration in a transaction so a failure mid-way leaves no
        # half-applied schema. The reversibility exit criterion depends on this.
        transaction_per_migration=True,
        compare_type=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection: Connection) -> None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    section: dict[str, Any] = config.get_section(config.config_ini_section) or {}
    engine = async_engine_from_config(section, prefix="sqlalchemy.")

    async with engine.connect() as connection:
        await connection.run_sync(_do_run)

    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
