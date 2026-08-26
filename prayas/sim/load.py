"""Load simulated cycles into the database (ADR-030, ADR-031).

Two connections, deliberately:

* the **app** engine writes observables into `events_raw` and runs the real
  `project_tenant`, so simulated data traverses exactly the code path live data
  does — the build list's "one code path, no drift";
* the **owner** engine writes `sim_ground_truth`, because `prayas_app` has no
  privilege on that table and must not acquire one (ADR-030).

The split is the leakage control, not a convenience.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.ingest.projector import project_tenant
from prayas.sim.generate import SimulatedCycle

log = logging.getLogger(__name__)


async def ensure_tenant(owner_engine: AsyncEngine, tenant_id: str) -> None:
    async with owner_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO tenants (tenant_id, name) VALUES (:t, :t)"
                " ON CONFLICT (tenant_id) DO NOTHING"
            ),
            {"t": tenant_id},
        )


async def write_mandates(owner_engine: AsyncEngine, cycles: list[SimulatedCycle]) -> None:
    """Mandates must exist before projection: the projector locks the row first."""
    async with owner_engine.begin() as conn:
        for cycle in cycles:
            await conn.execute(
                text(
                    "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail,"
                    " max_amount_paise, state, consent_ref, created_at)"
                    " VALUES (:mandate_id, :tenant_id, :customer_id, :rail,"
                    " :max_amount_paise, 'created', :consent_ref, :created_at)"
                    " ON CONFLICT (mandate_id) DO NOTHING"
                ),
                {
                    "mandate_id": cycle.mandate_id,
                    "tenant_id": cycle.tenant_id,
                    "customer_id": cycle.customer_id,
                    "rail": cycle.rail,
                    "max_amount_paise": max(cycle.amount_paise * 2, 1),
                    "consent_ref": f"consent_{cycle.mandate_id}",
                    "created_at": cycle.due_at,
                },
            )


async def write_ground_truth(
    owner_engine: AsyncEngine, cycles: list[SimulatedCycle], *, run_id: str, seed: int
) -> None:
    """Owner-only. `prayas_app` cannot read this table by construction."""
    async with owner_engine.begin() as conn:
        for cycle in cycles:
            await conn.execute(
                text(
                    "INSERT INTO sim_ground_truth (tenant_id, cycle_id, run_id, seed,"
                    " true_cause, true_funding_time, issuer_state, payday_archetype,"
                    " masked_as_05)"
                    " VALUES (:tenant_id, :cycle_id, :run_id, :seed, :true_cause,"
                    " :true_funding_time, :issuer_state, :payday_archetype, :masked_as_05)"
                    " ON CONFLICT (tenant_id, cycle_id) DO NOTHING"
                ),
                {
                    "tenant_id": cycle.tenant_id,
                    "cycle_id": cycle.cycle_id,
                    "run_id": run_id,
                    "seed": seed,
                    "true_cause": cycle.truth.true_cause,
                    "true_funding_time": cycle.truth.true_funding_time,
                    "issuer_state": cycle.truth.issuer_state,
                    "payday_archetype": cycle.truth.payday_archetype,
                    "masked_as_05": cycle.truth.masked_as_05,
                },
            )


async def write_observables(
    app_engine: AsyncEngine, cycles: list[SimulatedCycle], *, tenant_id: str
) -> int:
    """Insert the observable stream as the app role, exactly as ingest does."""
    written = 0
    async with tenant_transaction(app_engine, tenant_id) as conn:
        for cycle in cycles:
            for event in cycle.observables:
                body: dict[str, Any] = event["body"]
                await conn.execute(
                    text(
                        "INSERT INTO events_raw"
                        " (event_id, tenant_id, event_type, mandate_id, payload, signature_ok)"
                        " VALUES (:event_id, :tenant_id, :event_type, :mandate_id,"
                        "         CAST(:payload AS jsonb), true)"
                        " ON CONFLICT (event_id) DO NOTHING"
                    ),
                    {
                        "event_id": event["event_id"],
                        "tenant_id": tenant_id,
                        "event_type": body["event"],
                        "mandate_id": cycle.mandate_id,
                        "payload": json.dumps(body, separators=(",", ":"), ensure_ascii=False),
                    },
                )
                written += 1
    return written


async def load_run(
    app_engine: AsyncEngine,
    owner_engine: AsyncEngine,
    cycles: list[SimulatedCycle],
    *,
    tenant_id: str,
    run_id: str,
    seed: int,
    project: bool = True,
) -> int:
    """Load a generated run and project it through the production projector."""
    await ensure_tenant(owner_engine, tenant_id)
    await write_mandates(owner_engine, cycles)
    await write_ground_truth(owner_engine, cycles, run_id=run_id, seed=seed)
    written = await write_observables(app_engine, cycles, tenant_id=tenant_id)

    if project:
        # Drain in batches; project_tenant claims with SKIP LOCKED and returns
        # how many it consumed, so zero means the backlog is clear.
        while await project_tenant(app_engine, tenant_id, batch_size=500):
            pass

    log.info(
        "sim.run_loaded",
        extra={"run_id": run_id, "cycles": len(cycles), "events": written},
    )
    return written
