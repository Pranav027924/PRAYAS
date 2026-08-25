"""Async engine construction.

Two engines exist by design (ADR-004):

* the **app** engine connects as ``prayas_app``, which owns nothing and is
  therefore subject to row-level security unconditionally;
* the **owner** engine is used by Alembic for DDL and by nothing else.

Keeping them separate is what makes the isolation guarantee real rather than
conventional — the application physically cannot bypass a policy by ownership.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_app_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Engine for application traffic. Never holds DDL rights.

    ``pool_pre_ping`` is on because a stale pooled connection surfacing mid-decision
    is indistinguishable from a failure on the money path, and §17.4 requires that
    ambiguity be designed out rather than handled.
    """
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        # asyncpg caches prepared statements per connection. Harmless directly
        # against Postgres; revisit if a transaction-mode pooler is introduced,
        # since the cache and that pooling mode conflict.
        connect_args={"server_settings": {"application_name": "prayas-app"}},
    )


def create_owner_engine(url: str, *, echo: bool = False) -> AsyncEngine:
    """Engine for migrations only. Holds DDL rights; bypasses RLS by ownership."""
    return create_async_engine(
        url,
        echo=echo,
        pool_pre_ping=True,
        poolclass=None,
        connect_args={"server_settings": {"application_name": "prayas-migrate"}},
    )
