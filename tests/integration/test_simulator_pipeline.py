"""Simulator through the production pipeline (Master Spec §38; ADR-030, ADR-031).

Covers the two database-backed Phase 3 exit criteria — simulated events project
to valid state through the production projector, and 10,000 cycles generate
inside 60 seconds — plus the leakage guarantee and the no-drift fidelity check.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.api.main import app
from prayas.db.schema import APP_ROLE
from prayas.db.tenancy import system_transaction, tenant_transaction
from prayas.domain.states import CycleState, MandateState
from prayas.sim.config import SimConfig
from prayas.sim.generate import generate
from prayas.sim.load import load_run
from tests.conftest import WEBHOOK_SECRET_REF, requires_db, sign

pytestmark = [pytest.mark.db, requires_db]

TENANT = "t_alpha"


@pytest.fixture
async def clean_sim(owner_engine: AsyncEngine) -> AsyncIterator[None]:
    async def _wipe() -> None:
        async with owner_engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE sim_ground_truth, events_raw, cycles, attempts,"
                    " scheduled_actions, mandates, tenants RESTART IDENTITY CASCADE"
                )
            )

    await _wipe()
    yield
    await _wipe()


# ── exit criterion: simulated events project to valid state ────────────────


async def test_simulated_events_project_to_valid_state(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, clean_sim: None
) -> None:
    """Through the real `project_tenant` — one code path, no drift."""
    cycles = generate(SimConfig(), seed=2026, tenant_id=TENANT, cycles=150)
    await load_run(app_engine, owner_engine, cycles, tenant_id=TENANT, run_id="r1", seed=2026)

    async with tenant_transaction(app_engine, TENANT) as conn:
        rows = (
            await conn.execute(text("SELECT state, attempts_used, attempt_budget FROM cycles"))
        ).all()
        mandate_states = (await conn.execute(text("SELECT DISTINCT state FROM mandates"))).scalars()
        unprocessed = await conn.scalar(
            text("SELECT count(*) FROM events_raw WHERE processed_at IS NULL")
        )

    assert rows, "no cycles were projected"
    assert unprocessed == 0, "the projector left events unconsumed"

    valid_cycle_states = {s.value for s in CycleState}
    valid_mandate_states = {s.value for s in MandateState}

    for state, attempts_used, budget in rows:
        assert state in valid_cycle_states, f"projected an unknown cycle state: {state}"
        assert attempts_used <= budget, "the regulator's budget was exceeded"

    for state in mandate_states:
        assert state in valid_mandate_states, f"projected an unknown mandate state: {state}"


async def test_every_generated_cycle_reaches_the_database(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, clean_sim: None
) -> None:
    cycles = generate(SimConfig(), seed=11, tenant_id=TENANT, cycles=100)
    await load_run(app_engine, owner_engine, cycles, tenant_id=TENANT, run_id="r2", seed=11)

    async with tenant_transaction(app_engine, TENANT) as conn:
        projected = await conn.scalar(text("SELECT count(*) FROM cycles"))

    assert projected == len(cycles)


async def test_failed_cycles_consume_exactly_one_attempt(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, clean_sim: None
) -> None:
    """One failure event per cycle, so the projector must count exactly one."""
    cycles = generate(SimConfig(), seed=12, tenant_id=TENANT, cycles=120)
    await load_run(app_engine, owner_engine, cycles, tenant_id=TENANT, run_id="r3", seed=12)

    failed_ids = [c.cycle_id for c in cycles if c.truth.true_cause != "none"]
    assert failed_ids

    async with tenant_transaction(app_engine, TENANT) as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT cycle_id, attempts_used, state FROM cycles WHERE cycle_id = ANY(:ids)"
                ),
                {"ids": failed_ids},
            )
        ).all()

    for _cycle_id, attempts_used, state in rows:
        assert attempts_used == 1
        assert state == CycleState.EXECUTING.value


# ── exit criterion: 10,000 cycles in under 60 seconds ──────────────────────


@pytest.mark.slow
def test_ten_thousand_cycles_generate_within_budget() -> None:
    """Generation only — the criterion is about the generative process.

    Loading and projection are measured separately below, so a slow database
    cannot mask or inflate the generator's own cost.
    """
    started = time.perf_counter()
    cycles = generate(SimConfig(), seed=99, tenant_id=TENANT, cycles=10_000)
    elapsed = time.perf_counter() - started

    assert len(cycles) == 10_000
    assert elapsed < 60.0, f"10,000 cycles took {elapsed:.1f}s, budget is 60s"


# ── leakage: the app role cannot read ground truth (ADR-030) ───────────────


@pytest.mark.parametrize("privilege", ["SELECT", "INSERT", "UPDATE", "DELETE"])
async def test_app_role_holds_no_privilege_on_ground_truth(
    app_engine: AsyncEngine, privilege: str
) -> None:
    """§38: "models see only the observables." Enforced by grant, not by care.

    Asserted structurally so it fails the moment someone *grants* the privilege,
    without waiting for a feature query to use it.
    """
    async with system_transaction(app_engine) as conn:
        granted = await conn.scalar(
            text("SELECT has_table_privilege(:role, 'sim_ground_truth', :priv)"),
            {"role": APP_ROLE, "priv": privilege},
        )

    assert granted is False, (
        f"{APP_ROLE} holds {privilege} on sim_ground_truth — labels are reachable "
        f"from the model path (ADR-030)"
    )


async def test_reading_ground_truth_as_the_app_role_is_refused(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, clean_sim: None
) -> None:
    """The behavioural counterpart to the privilege assertion above."""
    cycles = generate(SimConfig(), seed=13, tenant_id=TENANT, cycles=20)
    await load_run(app_engine, owner_engine, cycles, tenant_id=TENANT, run_id="r4", seed=13)

    with pytest.raises(Exception) as exc:
        async with tenant_transaction(app_engine, TENANT) as conn:
            await conn.execute(text("SELECT count(*) FROM sim_ground_truth"))

    assert "permission denied" in str(exc.value).lower()


async def test_ground_truth_is_readable_by_the_evaluation_role(
    app_engine: AsyncEngine, owner_engine: AsyncEngine, clean_sim: None
) -> None:
    """§20's confusion matrix needs the labels — through the owner role."""
    cycles = generate(SimConfig(), seed=14, tenant_id=TENANT, cycles=40)
    await load_run(app_engine, owner_engine, cycles, tenant_id=TENANT, run_id="r5", seed=14)

    async with owner_engine.begin() as conn:
        stored = await conn.scalar(text("SELECT count(*) FROM sim_ground_truth"))
        causes = (
            await conn.execute(text("SELECT DISTINCT true_cause FROM sim_ground_truth"))
        ).scalars()

    assert stored == len(cycles)
    assert set(causes), "no causes recorded"


# ── no drift: the webhook path agrees with direct insertion (ADR-031) ──────


async def test_webhook_path_produces_identical_state_to_direct_load(
    app_engine: AsyncEngine,
    owner_engine: AsyncEngine,
    clean_sim: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proves "one code path, no drift" rather than assuming it.

    A divergence between the simulator's event construction and the webhook
    envelope parsing — how `cycle_ref` or `occurred_at` is derived — would
    otherwise produce a labelled dataset that does not match live traffic.
    """
    from tests.conftest import WEBHOOK_SECRET

    monkeypatch.setenv(f"PRAYAS_WEBHOOK_SECRET_{WEBHOOK_SECRET_REF}", WEBHOOK_SECRET)
    cycles = generate(SimConfig(), seed=15, tenant_id=TENANT, cycles=8)

    # Route A: direct insertion + projector.
    await load_run(app_engine, owner_engine, cycles, tenant_id=TENANT, run_id="direct", seed=15)

    async with tenant_transaction(app_engine, TENANT) as conn:
        direct = (
            await conn.execute(
                text(
                    "SELECT cycle_id, state, attempts_used, recovered_paise"
                    " FROM cycles ORDER BY cycle_id"
                )
            )
        ).all()

    # Route B: same events, through HMAC verification and the real endpoint.
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE sim_ground_truth, events_raw, cycles, attempts,"
                " scheduled_actions, mandates, tenants RESTART IDENTITY CASCADE"
            )
        )
        await conn.execute(
            text("INSERT INTO tenants (tenant_id, name) VALUES (:t, :t)"), {"t": TENANT}
        )
        await conn.execute(
            text("INSERT INTO webhook_secrets (tenant_id, secret_ref) VALUES (:t, :r)"),
            {"t": TENANT, "r": WEBHOOK_SECRET_REF},
        )

    from prayas.sim.load import write_mandates

    await write_mandates(owner_engine, cycles)

    app.state.engine = app_engine
    import json as _json

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://prayas.test"
    ) as client:
        for cycle in cycles:
            for event in cycle.observables:
                raw = _json.dumps(event["body"], separators=(",", ":"), ensure_ascii=False).encode()
                response = await client.post(
                    f"/v1/webhooks/razorpay/{TENANT}",
                    content=raw,
                    headers={
                        "x-razorpay-signature": sign(raw),
                        "x-razorpay-event-id": event["event_id"],
                        "content-type": "application/json",
                    },
                )
                assert response.status_code == 200, response.text

    from prayas.ingest.projector import project_tenant

    while await project_tenant(app_engine, TENANT, batch_size=500):
        pass

    async with tenant_transaction(app_engine, TENANT) as conn:
        via_webhook = (
            await conn.execute(
                text(
                    "SELECT cycle_id, state, attempts_used, recovered_paise"
                    " FROM cycles ORDER BY cycle_id"
                )
            )
        ).all()

    assert direct, "the direct route projected nothing"
    assert via_webhook == direct, "the webhook path and direct load diverged"
