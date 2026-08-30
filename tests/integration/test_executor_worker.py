"""The worker loop: per-tenant draining and fair rotation (ADR-042, §18)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.executor.provider import FakeProvider
from prayas.executor.worker import drain_tenant, run, tick
from tests.conftest import requires_db

pytestmark = [pytest.mark.db, requires_db]

FIRE_AT = datetime(2026, 3, 5, 3, 30, tzinfo=UTC)  # 09:00 IST, a legal window
_WIPE = (
    "TRUNCATE decisions, outbox, scheduled_actions, attempts, cycles,"
    " mandates, tenants RESTART IDENTITY CASCADE"
)


async def _seed_tenant(conn: object, tenant: str, n: int) -> None:
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tenants (tenant_id, name, config) VALUES"
            " (:t, :t, '{\"adoption_stage\": 4}'::jsonb)"
        ),
        {"t": tenant},
    )
    for i in range(n):
        m, c, a = f"{tenant}_m{i}", f"{tenant}_c{i}", f"{tenant}_a{i}"
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO mandates (mandate_id,tenant_id,customer_id,rail,"
                "max_amount_paise,state,consent_ref,created_at,mcc)"
                " VALUES (:m,:t,:cu,'upi_autopay',5000000,'active','c1',:n,'5411')"
            ),
            {"m": m, "t": tenant, "cu": f"cu{i}", "n": FIRE_AT},
        )
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO cycles (cycle_id,tenant_id,mandate_id,seq_no,amount_paise,"
                "due_at,deadline_at,attempt_budget,attempts_used,state,pdn_sent_at)"
                " VALUES (:c,:t,:m,1,49900,:n,:d,4,0,'executing',:p)"
            ),
            {
                "c": c,
                "t": tenant,
                "m": m,
                "n": FIRE_AT,
                "d": FIRE_AT + timedelta(days=20),
                "p": FIRE_AT - timedelta(hours=30),
            },
        )
        await conn.execute(  # type: ignore[attr-defined]
            text(
                "INSERT INTO scheduled_actions (action_id,tenant_id,cycle_id,mandate_id,"
                "action_type,fire_at,state,payload)"
                " VALUES (:a,:t,:c,:m,'debit_attempt',:f,'pending','{}'::jsonb)"
            ),
            {"a": a, "t": tenant, "c": c, "m": m, "f": FIRE_AT - timedelta(minutes=1)},
        )


@pytest.fixture
async def two_tenant_queues(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))
        await _seed_tenant(conn, "t_one", 3)
        await _seed_tenant(conn, "t_two", 2)
    yield
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))


async def test_drain_tenant_touches_only_its_own_tenant(
    app_engine: AsyncEngine, two_tenant_queues: None
) -> None:
    """Invariant 6 holds inside the executor, not just at the API edge."""
    provider = FakeProvider()
    result = await drain_tenant(app_engine, "t_one", provider, now=FIRE_AT)

    assert result.claimed == 3, f"claimed {result.claimed} for t_one, expected 3"

    async with tenant_transaction(app_engine, "t_two") as conn:
        untouched = await conn.scalar(
            text("SELECT count(*) FROM scheduled_actions WHERE state = 'pending'")
        )
    assert untouched == 2, "draining one tenant consumed another tenant's queue"


async def test_tick_drains_every_tenant(app_engine: AsyncEngine, two_tenant_queues: None) -> None:
    """§18's fair queuing: no merchant's burst starves another."""
    provider = FakeProvider()
    result = await tick(app_engine, provider, now=FIRE_AT)

    assert result.claimed == 5, f"claimed {result.claimed} across tenants, expected 5"
    assert result.fired == 5
    assert result.relayed == 5, "intents were committed but never relayed"

    for tenant, expected in (("t_one", 3), ("t_two", 2)):
        async with tenant_transaction(app_engine, tenant) as conn:
            sent = await conn.scalar(text("SELECT count(*) FROM outbox WHERE state = 'sent'"))
        assert sent == expected, f"{tenant}: {sent} relayed, expected {expected}"

    assert provider.distinct_debits == 5, "one debit per cycle, no more"


async def test_a_second_tick_finds_nothing_left(
    app_engine: AsyncEngine, two_tenant_queues: None
) -> None:
    """Settled work must not be re-claimed — that would be the double debit."""
    provider = FakeProvider()
    await tick(app_engine, provider, now=FIRE_AT)
    second = await tick(app_engine, provider, now=FIRE_AT)

    assert second.claimed == 0, f"a settled action was re-claimed ({second.claimed})"
    assert provider.distinct_debits == 5, "the second tick produced extra debits"


async def test_run_stops_when_asked(app_engine: AsyncEngine, two_tenant_queues: None) -> None:
    """The loop must honour its stop event, or a deploy hangs."""
    stop = asyncio.Event()
    task = asyncio.create_task(
        run(app_engine, FakeProvider(), poll_interval=0.05, stop=stop, now=FIRE_AT)
    )
    await asyncio.sleep(0.2)
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    async with tenant_transaction(app_engine, "t_one") as conn:
        remaining = await conn.scalar(
            text("SELECT count(*) FROM scheduled_actions WHERE state = 'pending'")
        )
    assert remaining == 0, "the loop stopped before doing any work"


async def test_a_failing_tick_does_not_kill_the_loop(
    app_engine: AsyncEngine, two_tenant_queues: None
) -> None:
    """A dead executor fires nothing, and that failure is silent.

    A transient error must be logged and survived rather than terminating the
    process.
    """

    class ExplodingProvider:
        calls = 0

        async def submit_debit(self, **_: object) -> object:
            ExplodingProvider.calls += 1
            raise RuntimeError("provider exploded")

        async def fetch_by_key(self, idem_key: str) -> object:
            return None

    stop = asyncio.Event()
    task = asyncio.create_task(
        run(app_engine, ExplodingProvider(), poll_interval=0.05, stop=stop, now=FIRE_AT)  # type: ignore[arg-type]
    )
    await asyncio.sleep(0.3)
    assert not task.done(), "the loop died on a provider error"
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert ExplodingProvider.calls > 0, "the failing path was never exercised"
