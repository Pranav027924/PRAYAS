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


# ── FINDING-P17-19: claim eligibility must not move with the gate's `now` ──

#: 08:30 IST, 24.5 hours before FIRE_AT (09:00 IST) — inside §30's contact
#: window, and a lawful notice-to-debit gap.
NOTICE_AT = FIRE_AT - timedelta(hours=24, minutes=30)
TWO_PASS_TENANT = "t_two_pass"


async def _seed_two_pass_cycle(conn: object) -> None:
    """One cycle carrying both a due notice and a debit not due yet — the
    seeder's `settle()` shape: the debit's own `fire_at` update comes later,
    once the notice pass has had its chance to claim on its own."""
    now = datetime.now(UTC)
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO tenants (tenant_id, name, config) VALUES"
            " (:t, :t, jsonb_build_object('adoption_stage', 4,"
            " 'dlt_template_id', 'DLT_TEST_V1', 'header_series', '160',"
            " 'dnd_registered', false, 'fatigue_cap', 4))"
        ),
        {"t": TWO_PASS_TENANT},
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO mandates (mandate_id,tenant_id,customer_id,rail,"
            "max_amount_paise,state,consent_ref,created_at,mcc)"
            " VALUES ('m1',:t,'cu1','upi_autopay',5000000,'active','c1',:n,'5411')"
        ),
        {"t": TWO_PASS_TENANT, "n": now},
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO cycles (cycle_id,tenant_id,mandate_id,seq_no,amount_paise,"
            "due_at,deadline_at,attempt_budget,attempts_used,state)"
            " VALUES ('c1',:t,'m1',1,49900,:n,:d,4,0,'executing')"
        ),
        {"t": TWO_PASS_TENANT, "n": now, "d": now + timedelta(days=20)},
    )
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO scheduled_actions (action_id,tenant_id,cycle_id,mandate_id,"
            "action_type,fire_at,state,payload)"
            " VALUES ('a_notice',:t,'c1','m1','pdn_notice',:f,'pending','{}'::jsonb)"
        ),
        {"t": TWO_PASS_TENANT, "f": now - timedelta(minutes=1)},
    )
    # Far in the future — not a real claim candidate until the second UPDATE
    # below moves it, mirroring `settle()`'s own two-step choreography.
    await conn.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO scheduled_actions (action_id,tenant_id,cycle_id,mandate_id,"
            "action_type,fire_at,state,payload)"
            " VALUES ('a_debit',:t,'c1','m1','debit_attempt',:f,'pending','{}'::jsonb)"
        ),
        {"t": TWO_PASS_TENANT, "f": now + timedelta(days=100)},
    )


async def test_a_future_gate_now_does_not_also_claim_a_still_pending_notice(
    app_engine: AsyncEngine, owner_engine: AsyncEngine
) -> None:
    """FINDING-P17-19.

    The seeder's `settle()` drains notices at a *past* simulated instant and
    debits at a *future* one, seconds apart in real time. Claim eligibility
    must stay tied to the tenant's own real clock regardless of which `now`
    a caller supplies for the gate's own reasoning — otherwise the debit
    pass's future-pointing `now` also claims whatever notice the earlier,
    past-pointing pass left pending, fires it at the debit's own instant, and
    `hours_since_pdn` reads zero instead of the true gap. That collapsed
    every debit in a full reseed to `RBI-EMANDATE-PDN-24H` DENY.
    """
    async with owner_engine.begin() as conn:
        await conn.execute(text(_WIPE))
        await _seed_two_pass_cycle(conn)

    provider = FakeProvider()
    first = await drain_tenant(app_engine, TWO_PASS_TENANT, provider, now=NOTICE_AT)
    assert first.claimed == 1, "the debit (fire_at 100 days out) must not be claimable yet"
    assert first.fired == 1, "the notice must have sent"

    async with tenant_transaction(app_engine, TWO_PASS_TENANT) as conn:
        pdn_sent_at = await conn.scalar(
            text("SELECT pdn_sent_at FROM cycles WHERE cycle_id = 'c1'")
        )
    assert pdn_sent_at == NOTICE_AT, (
        f"pdn_sent_at was {pdn_sent_at}, expected the notice pass's own instant {NOTICE_AT} — "
        "a later pass's `now` must never move it"
    )

    # Make the debit due for real, exactly as settle()'s second UPDATE does.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE scheduled_actions SET fire_at = now() - interval '1 minute'"
                " WHERE action_id = 'a_debit'"
            )
        )

    second = await drain_tenant(app_engine, TWO_PASS_TENANT, provider, now=FIRE_AT)
    assert second.claimed == 1, f"claimed {second.claimed}, expected exactly the debit"

    async with tenant_transaction(app_engine, TWO_PASS_TENANT) as conn:
        decision = (
            await conn.execute(
                text(
                    "SELECT verdict, rationale FROM decisions"
                    " WHERE tenant_id = :t AND action_type = 'debit_attempt'"
                    " ORDER BY ts DESC LIMIT 1"
                ),
                {"t": TWO_PASS_TENANT},
            )
        ).first()
    assert decision is not None
    assert decision.verdict == "ALLOW", (
        f"debit was {decision.verdict} ({decision.rationale}) — hours_since_pdn should read "
        "~24.5h, not 0, since the notice's own instant must not have moved"
    )
