"""Kill switches and the emergency stop (Master Spec §18, §45; ADR-084).

**Phase 15 exit criterion:** "Emergency stop executes in under 60 seconds."

Measured against a populated database rather than asserted by inspection, and
asserted to actually stop firing — a stop that returns quickly without stopping
anything would satisfy a timing test and nothing else.
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import system_transaction, tenant_transaction
from prayas.executor.killswitch import (
    MANDATE,
    PLATFORM,
    PLATFORM_TENANT,
    SCOPES,
    TENANT,
    KillSwitchError,
    is_stopped,
    resume,
    stop,
)
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

#: §45 — "executable in under sixty seconds by one person".
EMERGENCY_STOP_BUDGET_SECONDS = 60.0


# ── the three granularities ────────────────────────────────────────────────


async def test_nothing_is_stopped_by_default(
    app_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    tenant, _ = two_tenants
    async with tenant_transaction(app_engine, tenant) as conn:
        verdict = await is_stopped(conn, tenant_id=tenant, mandate_id="m1")
    assert not verdict
    assert verdict.scope is None


async def test_a_tenant_stop_does_not_touch_other_tenants(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """One merchant's configuration being wrong is not grounds to stop the
    others collecting."""
    tenant_a, tenant_b = two_tenants
    async with system_transaction(owner_engine) as conn:
        await stop(conn, scope=TENANT, tenant_id=tenant_a, reason="test")

        assert (await is_stopped(conn, tenant_id=tenant_a)).scope == TENANT
        assert not await is_stopped(conn, tenant_id=tenant_b)


async def test_a_mandate_stop_is_proportionate(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """One customer disputing should not stop the whole tenant."""
    tenant, _ = two_tenants
    async with system_transaction(owner_engine) as conn:
        await stop(conn, scope=MANDATE, tenant_id=tenant, mandate_id="m_disputed")

        assert (await is_stopped(conn, tenant_id=tenant, mandate_id="m_disputed")).scope == MANDATE
        assert not await is_stopped(conn, tenant_id=tenant, mandate_id="m_other")
        assert not await is_stopped(conn, tenant_id=tenant)


async def test_the_platform_stop_covers_every_tenant(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """A regulator saying stop must not require enumerating tenants — "most of
    it stopped" is not a state anyone wants to reason about at 3am."""
    tenant_a, tenant_b = two_tenants
    async with system_transaction(owner_engine) as conn:
        await stop(conn, scope=PLATFORM, reason="regulator instruction")

        for tenant in (tenant_a, tenant_b, "a_tenant_created_after_the_stop"):
            verdict = await is_stopped(conn, tenant_id=tenant)
            assert verdict.scope == PLATFORM, tenant


# ── fail closed ────────────────────────────────────────────────────────────


async def test_an_unreadable_switch_stops_firing() -> None:
    """**The load-bearing property.** An operator reaches for this during an
    incident, which is exactly when the database is least healthy. A switch
    that failed open would stop working when it is most needed."""

    class Broken:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("connection lost")

    from sqlalchemy.exc import SQLAlchemyError

    class BrokenSQL:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise SQLAlchemyError("connection lost")

    verdict = await is_stopped(BrokenSQL(), tenant_id="t_alpha")  # type: ignore[arg-type]
    assert verdict.stopped
    assert "failing closed" in verdict.reason


# ── the emergency stop, timed ──────────────────────────────────────────────


async def test_the_emergency_stop_completes_inside_its_budget(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """**Phase 15 exit criterion**, measured rather than asserted."""
    tenant, _ = two_tenants
    started = time.monotonic()
    async with system_transaction(owner_engine) as conn:
        await stop(conn, scope=PLATFORM, reason="drill")
    elapsed = time.monotonic() - started

    assert elapsed < EMERGENCY_STOP_BUDGET_SECONDS, f"took {elapsed:.1f}s"

    # And it actually stopped something — a fast no-op would pass a timing
    # test and nothing else.
    async with system_transaction(owner_engine) as conn:
        assert (await is_stopped(conn, tenant_id=tenant)).scope == PLATFORM


async def test_stopping_is_easier_than_restarting(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    """ADR-084's asymmetry, asserted as absence of capability. There is no
    `resume_all`: an accidental un-stop must not cost as little as the stop."""
    from prayas.executor import killswitch

    assert not hasattr(killswitch, "resume_all")
    assert not hasattr(killswitch, "restart_everything")

    tenant_a, tenant_b = two_tenants
    async with system_transaction(owner_engine) as conn:
        await stop(conn, scope=TENANT, tenant_id=tenant_a)
        await stop(conn, scope=TENANT, tenant_id=tenant_b)
        await resume(conn, scope=TENANT, tenant_id=tenant_a)

        assert not await is_stopped(conn, tenant_id=tenant_a)
        assert await is_stopped(conn, tenant_id=tenant_b), "resume must name its scope"


async def test_resume_lifts_only_the_named_mandate(
    owner_engine: AsyncEngine, two_tenants: tuple[str, str]
) -> None:
    tenant, _ = two_tenants
    async with system_transaction(owner_engine) as conn:
        await stop(conn, scope=MANDATE, tenant_id=tenant, mandate_id="m1")
        await stop(conn, scope=MANDATE, tenant_id=tenant, mandate_id="m2")
        await resume(conn, scope=MANDATE, tenant_id=tenant, mandate_id="m1")

        assert not await is_stopped(conn, tenant_id=tenant, mandate_id="m1")
        assert await is_stopped(conn, tenant_id=tenant, mandate_id="m2")


# ── argument discipline ────────────────────────────────────────────────────


@pytest.mark.parametrize("scope", SCOPES)
def test_every_scope_is_named(scope: str) -> None:
    assert scope in {PLATFORM, TENANT, MANDATE}


async def test_an_unknown_scope_is_refused(owner_engine: AsyncEngine) -> None:
    async with system_transaction(owner_engine) as conn:
        with pytest.raises(KillSwitchError, match="unknown scope"):
            await stop(conn, scope="everything_everywhere")


async def test_a_scoped_stop_requires_its_identifiers(owner_engine: AsyncEngine) -> None:
    async with system_transaction(owner_engine) as conn:
        with pytest.raises(KillSwitchError, match="requires a tenant_id"):
            await stop(conn, scope=TENANT)
        with pytest.raises(KillSwitchError, match="requires a tenant_id and a mandate_id"):
            await stop(conn, scope=MANDATE, tenant_id="t_alpha")


async def test_the_platform_switch_lives_in_a_reserved_tenant(
    owner_engine: AsyncEngine,
) -> None:
    """§36 already puts kill switches in `tenants.config`. One storage location
    means one thing to check when the question is "is anything stopped"."""
    async with system_transaction(owner_engine) as conn:
        await stop(conn, scope=PLATFORM, reason="drill")
        row = (
            await conn.execute(
                text("SELECT config FROM tenants WHERE tenant_id = :t"),
                {"t": PLATFORM_TENANT},
            )
        ).one()

    assert row._mapping["config"]["stopped"] is True
    assert row._mapping["config"]["reason"] == "drill"
    assert datetime.fromisoformat(row._mapping["config"]["stopped_at"]).tzinfo is not None
