"""Metatests: the isolation suite cannot silently stop covering a table.

the tenancy rules require that any new tenant-scoped table be added to
the isolation suite in the same commit that creates it. A rule stated in prose is
a hope. These tests read the live database and fail if reality and the declared
registry disagree, which turns that rule into machinery.

Partitions are excluded throughout: `decisions_202601` carries a `tenant_id`
column but is an implementation detail of `decisions`, whose policies govern it.
"""

from __future__ import annotations

import pytest
from sqlalchemy import TextClause, text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.schema import APP_ROLE, GLOBAL_TABLES, TENANT_SCOPED_TABLES
from prayas.db.tenancy import system_transaction
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

# Alembic's bookkeeping table is neither tenant-scoped nor a domain table.
_EXCLUDED = frozenset({"alembic_version"})

_TABLES_WITH_TENANT_COLUMN = text(
    """
    SELECT DISTINCT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND NOT c.relispartition
      AND a.attname = 'tenant_id'
      AND a.attnum > 0
      AND NOT a.attisdropped
    """
)

_ALL_BASE_TABLES = text(
    """
    SELECT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND NOT c.relispartition
    """
)


async def _names(engine: AsyncEngine, stmt: TextClause) -> set[str]:
    async with system_transaction(engine) as conn:
        result = await conn.execute(stmt)
        names: set[str] = {str(row[0]) for row in result}
        return names - _EXCLUDED


async def test_registry_matches_reality(owner_engine: AsyncEngine) -> None:
    """Every table with a tenant_id column is declared, and vice versa."""
    actual = await _names(owner_engine, _TABLES_WITH_TENANT_COLUMN)

    undeclared = actual - TENANT_SCOPED_TABLES
    assert not undeclared, (
        f"Tables carry tenant_id but are absent from TENANT_SCOPED_TABLES: "
        f"{sorted(undeclared)}. Add them to prayas/db/schema.py and give them a "
        f"policy in the same commit (tenancy rules)."
    )

    missing = TENANT_SCOPED_TABLES - actual
    assert not missing, f"Declared tenant-scoped but absent from the database: {sorted(missing)}"


async def test_every_table_is_classified(owner_engine: AsyncEngine) -> None:
    """No table may exist unclassified. A new table is a deliberate decision."""
    actual = await _names(owner_engine, _ALL_BASE_TABLES)
    classified = TENANT_SCOPED_TABLES | GLOBAL_TABLES

    unclassified = actual - classified
    assert not unclassified, (
        f"Unclassified tables: {sorted(unclassified)}. Every table is either "
        f"tenant-scoped or global by decision, never by default."
    )


async def test_rls_is_enabled_and_forced_everywhere(owner_engine: AsyncEngine) -> None:
    """ENABLE alone does not bind the owner; FORCE is what closes that gap (ADR-004)."""
    async with system_transaction(owner_engine) as conn:
        result = await conn.execute(
            text(
                """
                SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public' AND c.relname = ANY(:names)
                """
            ),
            {"names": sorted(TENANT_SCOPED_TABLES)},
        )
        rows = {row[0]: (row[1], row[2]) for row in result}

    not_enabled = [t for t, (enabled, _) in rows.items() if not enabled]
    not_forced = [t for t, (_, forced) in rows.items() if not forced]

    assert not not_enabled, f"RLS not enabled: {sorted(not_enabled)}"
    assert not not_forced, (
        f"RLS enabled but not FORCEd: {sorted(not_forced)}. Without FORCE the "
        f"table owner bypasses every policy and the isolation tests prove nothing."
    )


async def test_every_tenant_scoped_table_has_a_policy(owner_engine: AsyncEngine) -> None:
    """RLS with no policy denies everything; RLS with a policy is the actual control."""
    async with system_transaction(owner_engine) as conn:
        result = await conn.execute(
            text("SELECT tablename FROM pg_policies WHERE schemaname = 'public'")
        )
        with_policy = {row[0] for row in result}

    missing = TENANT_SCOPED_TABLES - with_policy
    assert not missing, f"Tenant-scoped tables with no RLS policy: {sorted(missing)}"


@pytest.mark.parametrize("privilege", ["UPDATE", "DELETE", "TRUNCATE"])
async def test_app_role_cannot_mutate_the_ledger(owner_engine: AsyncEngine, privilege: str) -> None:
    """Invariant 5, asserted structurally rather than behaviourally.

    Complements the behavioural test in test_tenant_isolation.py: this one fails
    the moment someone *grants* the privilege, without waiting for code to use it.
    """
    async with system_transaction(owner_engine) as conn:
        granted = await conn.scalar(
            text("SELECT has_table_privilege(:role, 'decisions', :priv)"),
            {"role": APP_ROLE, "priv": privilege},
        )

    assert granted is False, (
        f"{APP_ROLE} holds {privilege} on decisions. The ledger is append-only "
        f"(Master Spec §32, Invariant 5)."
    )
