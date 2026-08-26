"""Tenant isolation, treated as a correctness property (Master Spec §18, §40.4).

This suite is the headline Phase 0 exit criterion. It runs as ``prayas_app`` —
a non-owner role — so the policies apply unconditionally (ADR-004). Running it
as the owner would make every assertion here vacuous.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.schema import APP_READABLE_TENANT_TABLES
from prayas.db.tenancy import system_transaction, tenant_transaction
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

# sim_ground_truth is excluded: the app role has no privilege on it by
# design (ADR-030), so a row-visibility assertion would hit a permission
# error rather than test RLS. Its policing is asserted in the metatests.
TABLES = sorted(APP_READABLE_TENANT_TABLES)


@pytest.mark.parametrize("table", TABLES)
async def test_bound_tenant_sees_exactly_its_own_rows(
    app_engine: AsyncEngine, two_tenants: tuple[str, str], table: str
) -> None:
    """A bound tenant sees its row and precisely none of the other tenant's.

    Asserting an exact count of 1 rather than "no rows from B" matters: a broken
    connection or a policy that denies everything would satisfy the weaker form.
    """
    tenant_a, tenant_b = two_tenants

    async with tenant_transaction(app_engine, tenant_a) as conn:
        total = await conn.scalar(text(f"SELECT count(*) FROM {table}"))
        foreign = await conn.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id = :other"),
            {"other": tenant_b},
        )

    assert total == 1, f"{table}: expected exactly the bound tenant's row, saw {total}"
    assert foreign == 0, f"{table}: leaked {foreign} rows belonging to {tenant_b}"


@pytest.mark.parametrize("table", TABLES)
async def test_unbound_context_sees_nothing(
    app_engine: AsyncEngine, two_tenants: tuple[str, str], table: str
) -> None:
    """With no tenant bound, policies match nothing and the table reads empty.

    This is ADR-007's fail-closed behaviour: `current_setting(..., true)` yields
    NULL, NULL never equals a tenant_id, so no row is visible.
    """
    async with system_transaction(app_engine) as conn:
        visible = await conn.scalar(text(f"SELECT count(*) FROM {table}"))

    expected = 0
    if table == "experiment_config":
        # ADR-012 admits platform-scoped rows (tenant_id IS NULL) to everyone,
        # but the fixture seeds none, so this table is empty here too.
        expected = 0

    assert visible == expected, f"{table}: {visible} rows visible without tenant context"


async def test_cannot_write_a_row_stamped_for_another_tenant(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """WITH CHECK blocks cross-tenant writes.

    §18 shows only USING, which governs reads. Without WITH CHECK a tenant could
    insert rows attributed to another tenant — a cross-tenant write, worse than
    the read the section exists to prevent.
    """
    tenant_a, tenant_b = two_tenants

    with pytest.raises(Exception) as exc:
        async with tenant_transaction(app_engine, tenant_a) as conn:
            await conn.execute(
                text(
                    "INSERT INTO scheduled_actions"
                    " (action_id, tenant_id, action_type, fire_at, payload)"
                    " VALUES ('smuggled', :other, 'retry', now(), '{}'::jsonb)"
                ),
                {"other": tenant_b},
            )

    assert "row-level security" in str(exc.value).lower()


async def test_platform_scoped_experiment_is_visible_to_every_tenant(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """ADR-012: a NULL tenant_id row in experiment_config is platform-scoped.

    §33 randomises at customer level and one customer may hold mandates with
    several merchants, so an experiment that belongs to no single tenant must be
    readable by all of them.
    """
    tenant_a, tenant_b = two_tenants

    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO experiment_config"
                " (experiment_id, tenant_id, seed, control_pct, git_commit, committed_at)"
                " VALUES ('platform_wide', NULL, 'seed', 0.100, 'abc123', now())"
            )
        )

    for tenant in (tenant_a, tenant_b):
        async with tenant_transaction(app_engine, tenant) as conn:
            visible = await conn.scalar(
                text("SELECT count(*) FROM experiment_config WHERE experiment_id = 'platform_wide'")
            )
        assert visible == 1, f"{tenant} cannot see the platform-scoped experiment"


async def test_ledger_is_append_only_for_the_app_role(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Invariant 5: `decisions` accretes only.

    Enforced by privilege, not by convention — the app role was granted SELECT
    and INSERT and nothing else (ADR-004). §36's `REVOKE UPDATE, DELETE` is a
    no-op, since the privilege was never granted in the first place.

    SQL is assembled from a constant rather than written inline because the
    table name is shared between the two statements under test.
    """
    tenant_a, _ = two_tenants
    ledger = "decisions"

    for statement in (
        f"UPDATE {ledger} SET outcome = 'tampered'",
        f"DELETE FROM {ledger}",
    ):
        with pytest.raises(Exception) as exc:
            async with tenant_transaction(app_engine, tenant_a) as conn:
                await conn.execute(text(statement))

        assert "permission denied" in str(exc.value).lower(), (
            f"expected privilege denial for: {statement}"
        )


async def test_tenant_context_does_not_leak_between_transactions(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """Context is transaction-local, so a pooled connection cannot carry it forward.

    This is the specific failure `set_config(..., is_local => true)` prevents: a
    session-scoped SET would survive into whichever request borrowed the
    connection next, which §18 classes as an incident.
    """
    tenant_a, _ = two_tenants

    async with tenant_transaction(app_engine, tenant_a) as conn:
        assert await conn.scalar(text("SELECT current_setting('app.tenant_id', true)")) == tenant_a

    # A fresh transaction on the same pool must start with no tenant bound.
    async with system_transaction(app_engine) as conn:
        leaked = await conn.scalar(text("SELECT current_setting('app.tenant_id', true)"))

    assert not leaked, f"tenant context leaked across transactions: {leaked!r}"


async def test_foreign_keys_to_tenants_resolve_under_rls(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """ADR-013 regression: RLS on `tenants` must not break referential integrity.

    Seven tables carry `REFERENCES tenants(tenant_id)` (ADR-008). Bringing
    `tenants` under RLS raised the question of whether an FK check could still
    see the parent row. It can — referential integrity triggers execute as the
    table owner and are not subject to RLS — but that is exactly the kind of
    reasoning that should be pinned by a test rather than trusted.
    """
    tenant_a, _ = two_tenants

    async with tenant_transaction(app_engine, tenant_a) as conn:
        await conn.execute(
            text(
                "INSERT INTO scheduled_actions"
                " (action_id, tenant_id, action_type, fire_at, payload)"
                " VALUES ('fk_probe', :t, 'retry', now(), '{}'::jsonb)"
            ),
            {"t": tenant_a},
        )
        written = await conn.scalar(
            text("SELECT count(*) FROM scheduled_actions WHERE action_id = 'fk_probe'")
        )

    assert written == 1, "FK to tenants blocked a legitimate same-tenant insert under RLS"


async def test_tenant_sees_only_its_own_registry_row(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """ADR-013: `config` holds policy weights and kill switches (§18).

    Before this policy existed, a bound tenant could read every merchant's name
    and configuration.
    """
    tenant_a, tenant_b = two_tenants

    async with tenant_transaction(app_engine, tenant_a) as conn:
        rows = (await conn.execute(text("SELECT tenant_id FROM tenants"))).fetchall()

    assert [row[0] for row in rows] == [tenant_a], (
        f"{tenant_a} can see registry rows for other tenants: {[r[0] for r in rows]}"
    )
    assert tenant_b not in [row[0] for row in rows]
