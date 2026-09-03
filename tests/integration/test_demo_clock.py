"""Time travel, and the one guard it cannot be reached without (Demo spec
Phase 6, N2).

`prayas.demo.clock` is the one place in this codebase that writes into the
money path's notion of "now". Everything here is built around the single
fact that has to hold no matter what a caller passes in: a tenant not seeded
`config.demo_tenant: true` cannot have its clock moved, by any of the three
write paths, including the HTTP endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app
from prayas.console.auth import SECRET_ENV, issue
from prayas.db.tenancy import tenant_transaction
from prayas.demo.clock import (
    MAX_ADVANCE_SECONDS,
    ClockError,
    advance,
    jump_to_next_action,
    now_for,
    offset_seconds_for,
    reset,
)
from prayas.executor.claiming import claim_due_actions
from prayas.models.live import PriorTable
from prayas.planner.worker import plan_tenant
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

REAL_TENANT = "t_clock_real"
DEMO_TENANT = "t_clock_demo"
SECRET = "demo_clock_test_secret_0000000"
RICH_PRIORS = PriorTable(cells={}, global_hazard=0.42)


@pytest.fixture(autouse=True)
def _secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_ENV, SECRET)


async def _wipe(engine: AsyncEngine, tenant: str) -> None:
    async with engine.begin() as conn:
        for table in ("scheduled_actions", "cycles", "mandates", "demo_clock_state", "tenants"):
            await conn.execute(text(f"DELETE FROM {table} WHERE tenant_id = :t"), {"t": tenant})


async def _seed_tenant(engine: AsyncEngine, tenant: str, *, demo: bool) -> None:
    await _wipe(engine, tenant)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name, config) VALUES"
                " (:t, :t, jsonb_build_object('adoption_stage', 4,"
                " 'demo_tenant', CAST(:d AS boolean)))"
            ),
            {"t": tenant, "d": demo},
        )


async def _seed_cycle_due_in(
    engine: AsyncEngine, tenant: str, *, hours_until_deadline: float
) -> None:
    """One mandate, one executing cycle whose deadline is `hours_until_deadline`
    away from the real clock — a candidate under the real clock, and (when the
    virtual clock is pushed past it) not one under the tenant's own."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                " max_amount_paise, state, consent_ref, created_at)"
                " VALUES (:m, :t, 'cust_1', 'upi_autopay', 1500000, 'active', 'c1', :now)"
            ),
            {"m": f"sub_{tenant}", "t": tenant, "now": now},
        )
        await conn.execute(
            text(
                "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
                " due_at, deadline_at, attempt_budget, attempts_used, state)"
                " VALUES (:c, :t, :m, 1, 250000, :due, :dl, 4, 1, 'executing')"
            ),
            {
                "c": f"inv_{tenant}",
                "t": tenant,
                "m": f"sub_{tenant}",
                "due": now,
                "dl": now + timedelta(hours=hours_until_deadline),
            },
        )


async def _seed_action_due_in(engine: AsyncEngine, tenant: str, *, hours: float) -> datetime:
    """Returns the exact `fire_at` seeded, so a caller can assert against the
    real stored value rather than re-deriving it from a later clock read."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                " max_amount_paise, state, consent_ref, created_at)"
                " VALUES (:m, :t, 'cust_1', 'upi_autopay', 1500000, 'active', 'c1', :now)"
            ),
            {"m": f"sub_{tenant}", "t": tenant, "now": now},
        )
        await conn.execute(
            text(
                "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
                " due_at, deadline_at, attempt_budget, attempts_used, state)"
                " VALUES (:c, :t, :m, 1, 250000, :due, :dl, 4, 0, 'executing')"
            ),
            {
                "c": f"inv_{tenant}",
                "t": tenant,
                "m": f"sub_{tenant}",
                "due": now,
                "dl": now + timedelta(days=5),
            },
        )
        fire_at = now + timedelta(hours=hours)
        await conn.execute(
            text(
                "INSERT INTO scheduled_actions (action_id, tenant_id, cycle_id, mandate_id,"
                " action_type, fire_at, state, payload)"
                " VALUES (:a, :t, :c, :m, 'debit_attempt', :f, 'pending', '{}'::jsonb)"
            ),
            {
                "a": f"act_{tenant}",
                "t": tenant,
                "c": f"inv_{tenant}",
                "m": f"sub_{tenant}",
                "f": fire_at,
            },
        )
    return fire_at


# ── the guard: no path through this module moves a real tenant's clock ─────


@pytest.mark.asyncio
async def test_advance_refuses_a_tenant_without_the_demo_flag(owner_engine: AsyncEngine) -> None:
    await _seed_tenant(owner_engine, REAL_TENANT, demo=False)
    async with tenant_transaction(owner_engine, REAL_TENANT) as conn:
        with pytest.raises(ClockError):
            await advance(conn, REAL_TENANT, seconds=3600)


@pytest.mark.asyncio
async def test_reset_refuses_a_tenant_without_the_demo_flag(owner_engine: AsyncEngine) -> None:
    await _seed_tenant(owner_engine, REAL_TENANT, demo=False)
    async with tenant_transaction(owner_engine, REAL_TENANT) as conn:
        with pytest.raises(ClockError):
            await reset(conn, REAL_TENANT)


@pytest.mark.asyncio
async def test_jump_refuses_a_tenant_without_the_demo_flag(owner_engine: AsyncEngine) -> None:
    await _seed_tenant(owner_engine, REAL_TENANT, demo=False)
    async with tenant_transaction(owner_engine, REAL_TENANT) as conn:
        with pytest.raises(ClockError):
            await jump_to_next_action(conn, REAL_TENANT)


@pytest.mark.asyncio
async def test_absurd_advance_is_refused_even_on_a_demo_tenant(owner_engine: AsyncEngine) -> None:
    """A typo — or a client bug — cannot send a tenant's clock decades forward."""
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        with pytest.raises(ClockError):
            await advance(conn, DEMO_TENANT, seconds=MAX_ADVANCE_SECONDS + 1)


# ── a demo tenant's offset is real, and is what the money path reads ───────


@pytest.mark.asyncio
async def test_advance_moves_the_offset_and_now_for_reads_it_back(
    owner_engine: AsyncEngine,
) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        returned = await advance(conn, DEMO_TENANT, seconds=3600)
        offset = await offset_seconds_for(conn, DEMO_TENANT)

    assert offset == 3600
    expected = datetime.now(UTC) + timedelta(hours=1)
    assert abs((returned - expected).total_seconds()) < 5
    assert abs((now_for(offset) - expected).total_seconds()) < 5


@pytest.mark.asyncio
async def test_advances_accumulate(owner_engine: AsyncEngine) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        await advance(conn, DEMO_TENANT, seconds=3600)
        await advance(conn, DEMO_TENANT, seconds=3600)
        offset = await offset_seconds_for(conn, DEMO_TENANT)
    assert offset == 7200


@pytest.mark.asyncio
async def test_reset_zeroes_an_accumulated_offset(owner_engine: AsyncEngine) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        await advance(conn, DEMO_TENANT, seconds=86400)
        await reset(conn, DEMO_TENANT)
        offset = await offset_seconds_for(conn, DEMO_TENANT)
    assert offset == 0


@pytest.mark.asyncio
async def test_jump_to_next_action_lands_just_past_the_scheduled_instant(
    owner_engine: AsyncEngine,
) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    fire_at = await _seed_action_due_in(owner_engine, DEMO_TENANT, hours=1.5)

    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        new_now = await jump_to_next_action(conn, DEMO_TENANT, cycle_id=f"inv_{DEMO_TENANT}")

    assert new_now >= fire_at
    assert (new_now - fire_at).total_seconds() < 5


@pytest.mark.asyncio
async def test_jump_to_next_action_with_nothing_pending_raises(owner_engine: AsyncEngine) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        with pytest.raises(ClockError):
            await jump_to_next_action(conn, DEMO_TENANT, cycle_id="inv_nothing")


# ── the three call sites actually read it ───────────────────────────────────


@pytest.mark.asyncio
async def test_claim_due_actions_is_blind_to_a_virtually_future_action(
    owner_engine: AsyncEngine,
) -> None:
    """Real time has not reached `fire_at`, and neither has this claim."""
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    await _seed_action_due_in(owner_engine, DEMO_TENANT, hours=2)

    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        claimed = await claim_due_actions(conn, DEMO_TENANT, now=datetime.now(UTC))
    assert claimed == []


@pytest.mark.asyncio
async def test_claim_due_actions_sees_it_once_the_clock_is_advanced_past_it(
    owner_engine: AsyncEngine,
) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    await _seed_action_due_in(owner_engine, DEMO_TENANT, hours=2)

    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        await advance(conn, DEMO_TENANT, seconds=int(timedelta(hours=2, minutes=5).total_seconds()))
        virtual_now = now_for(await offset_seconds_for(conn, DEMO_TENANT))
        claimed = await claim_due_actions(conn, DEMO_TENANT, now=virtual_now)

    assert len(claimed) == 1
    assert claimed[0].action_id == f"act_{DEMO_TENANT}"


@pytest.mark.asyncio
async def test_plan_tenant_reads_the_tenants_own_virtual_clock(owner_engine: AsyncEngine) -> None:
    """The planner's `_CANDIDATES` query filters `deadline_at > :now`. Advance
    a demo tenant's clock past a cycle's deadline and the planner must stop
    considering it — proving `plan_tenant` resolved `now` from the tenant's
    offset rather than the wall clock, with no explicit `now` passed in."""
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    await _seed_cycle_due_in(owner_engine, DEMO_TENANT, hours_until_deadline=1)

    # Control: under the real clock, the cycle is still a valid candidate.
    result = await plan_tenant(owner_engine, DEMO_TENANT, RICH_PRIORS)
    assert result.considered == 1

    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    await _seed_cycle_due_in(owner_engine, DEMO_TENANT, hours_until_deadline=1)
    async with tenant_transaction(owner_engine, DEMO_TENANT) as conn:
        await advance(conn, DEMO_TENANT, seconds=int(timedelta(hours=2).total_seconds()))

    # Same cycle, same real clock — but this tenant's own clock now reads
    # past the deadline, and the planner must not schedule against it.
    result = await plan_tenant(owner_engine, DEMO_TENANT, RICH_PRIORS)
    assert result.considered == 0


# ── the HTTP endpoint carries the same guard ────────────────────────────────


@pytest.fixture
async def client(app_engine: AsyncEngine):  # type: ignore[no-untyped-def]
    app.state.engine = app_engine
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://clock") as c:
        yield c


def _auth(tenant: str, role: str = "compliance_reviewer") -> dict[str, str]:
    return {"Authorization": f"Bearer {issue(tenant, role, secret=SECRET.encode())}"}


@pytest.mark.asyncio
async def test_the_endpoint_refuses_a_tenant_without_the_demo_flag(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    await _seed_tenant(owner_engine, REAL_TENANT, demo=False)
    response = await client.post(
        "/v1/demo/clock/advance", json={"seconds": 3600}, headers=_auth(REAL_TENANT)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_the_endpoint_advances_a_demo_tenant(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    response = await client.post(
        "/v1/demo/clock/advance", json={"seconds": 3600}, headers=_auth(DEMO_TENANT)
    )
    assert response.status_code == 200
    body = response.json()
    assert body["offset_seconds"] == 3600
    assert body["tenant_id"] == DEMO_TENANT


@pytest.mark.asyncio
async def test_the_endpoint_rejects_a_non_integer_seconds(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    response = await client.post(
        "/v1/demo/clock/advance", json={"seconds": "soon"}, headers=_auth(DEMO_TENANT)
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_the_endpoint_resets(client: AsyncClient, owner_engine: AsyncEngine) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    await client.post("/v1/demo/clock/advance", json={"seconds": 86400}, headers=_auth(DEMO_TENANT))
    response = await client.post(
        "/v1/demo/clock/advance", json={"reset": True}, headers=_auth(DEMO_TENANT)
    )
    assert response.status_code == 200
    assert response.json()["offset_seconds"] == 0


@pytest.mark.asyncio
async def test_the_endpoint_jumps_to_the_next_action(
    client: AsyncClient, owner_engine: AsyncEngine
) -> None:
    await _seed_tenant(owner_engine, DEMO_TENANT, demo=True)
    await _seed_action_due_in(owner_engine, DEMO_TENANT, hours=1)
    response = await client.post(
        "/v1/demo/clock/advance",
        json={"jump_to_next_action": True, "cycle_id": f"inv_{DEMO_TENANT}"},
        headers=_auth(DEMO_TENANT),
    )
    assert response.status_code == 200
    assert response.json()["offset_seconds"] >= int(timedelta(hours=1).total_seconds())
