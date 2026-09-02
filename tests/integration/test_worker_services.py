"""The projector and aggregator services (ADR-093, ADR-097).

Both were the same failure before they existed: a correct library that no
process ran (FINDING-P17-04, FINDING-P17-07). These tests exist so that a
future refactor cannot quietly return them to that state — each asserts the
service *moves state*, not merely that it runs without raising.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.ingest.worker import tick as projector_tick
from prayas.memory.worker import collect_tenant
from prayas.memory.worker import tick as aggregator_tick

NOW = datetime.now(UTC)


async def _wipe(conn) -> None:  # type: ignore[no-untyped-def]
    for table in ("scheduled_actions", "attempts", "cycles", "events_raw", "mandates", "tenants"):
        await conn.execute(text(f"DELETE FROM {table}"))
    await conn.execute(text("DELETE FROM segment_priors"))


async def _tenant(conn, tenant: str, *, events: int, success_every: int = 3) -> None:  # type: ignore[no-untyped-def]
    """A tenant with a mandate, a cycle, and `events` observed outcomes."""
    await conn.execute(
        text(
            "INSERT INTO tenants (tenant_id, name, config)"
            " VALUES (:t, :t, '{\"adoption_stage\": 0}'::jsonb)"
        ),
        {"t": tenant},
    )
    mandate = f"sub_{tenant}"
    await conn.execute(
        text(
            "INSERT INTO mandates (mandate_id, tenant_id, customer_id, rail, mcc,"
            " max_amount_paise, state, consent_ref, created_at)"
            " VALUES (:m, :t, 'c1', 'upi_autopay', '5411', 1500000, 'created', 'r1', :now)"
        ),
        {"m": mandate, "t": tenant, "now": NOW},
    )
    await conn.execute(
        text(
            "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
            " due_at, deadline_at, attempt_budget, attempts_used, state)"
            " VALUES (:c, :t, :m, 1, 250000, :due, :dl, 4, 0, 'executing')"
        ),
        {
            "c": f"cyc_{tenant}",
            "t": tenant,
            "m": mandate,
            "due": NOW,
            "dl": NOW + timedelta(days=20),
        },
    )
    for i in range(events):
        succeeded = i % success_every == 0
        body = {
            "event": "payment.captured" if succeeded else "payment.failed",
            "payload": {
                "subscription": {
                    "entity": {"id": mandate, "current_end": int(NOW.timestamp()) + 10**6}
                },
                "payment": {
                    "entity": {
                        "id": f"pay_{tenant}_{i}",
                        "amount": 250000,
                        "invoice_id": f"cyc_{tenant}",
                    }
                },
            },
            "created_at": int(NOW.timestamp()),
        }
        await conn.execute(
            text(
                "INSERT INTO events_raw (event_id, tenant_id, event_type, mandate_id,"
                " payload, signature_ok)"
                " VALUES (:e, :t, :ty, :m, CAST(:p AS jsonb), true)"
            ),
            {
                "e": f"evt_{tenant}_{i}",
                "t": tenant,
                "ty": body["event"],
                "m": mandate,
                "p": json.dumps(body),
            },
        )


# ── projector ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_projector_drains_events_and_advances_the_watermark(
    owner_engine: AsyncEngine,
) -> None:
    """The service must move state, not merely run.

    Before this existed the stack was green and did nothing: webhooks landed
    in `events_raw` and stayed there forever (FINDING-P17-04).
    """
    async with owner_engine.begin() as conn:
        await _wipe(conn)
        await _tenant(conn, "t_proj", events=6)

    async with owner_engine.begin() as conn:
        pending = (
            (
                await conn.execute(
                    text("SELECT count(*) c FROM events_raw WHERE processed_at IS NULL")
                )
            )
            .one()
            .c
        )
    assert pending == 6

    result = await projector_tick(owner_engine)
    assert result.projected == 6

    async with owner_engine.begin() as conn:
        still_pending = (
            (
                await conn.execute(
                    text("SELECT count(*) c FROM events_raw WHERE processed_at IS NULL")
                )
            )
            .one()
            .c
        )
        cycles = (await conn.execute(text("SELECT count(*) c FROM cycles"))).one().c
    assert still_pending == 0
    assert cycles >= 1


@pytest.mark.asyncio
async def test_projector_is_idempotent_on_a_second_pass(owner_engine: AsyncEngine) -> None:
    """A drained log yields nothing, and nothing is double-counted."""
    async with owner_engine.begin() as conn:
        await _wipe(conn)
        await _tenant(conn, "t_proj2", events=4)

    first = await projector_tick(owner_engine)
    second = await projector_tick(owner_engine)
    assert first.projected == 4
    assert second.projected == 0


# ── aggregator ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aggregator_collects_observations_from_the_event_log(
    owner_engine: AsyncEngine,
) -> None:
    """ADR-097: the source is `events_raw`, not `attempts`.

    In OBSERVE the executor has fired nothing, so `attempts` is empty exactly
    when priors are most needed. Reading it instead would deadlock.
    """
    async with owner_engine.begin() as conn:
        await _wipe(conn)
        await _tenant(conn, "t_agg", events=9, success_every=3)

    observations = await collect_tenant(owner_engine, "t_agg")
    assert observations, "no observations collected from the event log"
    assert sum(o.attempts for o in observations) == 9
    assert sum(o.successes for o in observations) == 3
    assert all(o.tenant_id == "t_agg" for o in observations)


@pytest.mark.asyncio
async def test_one_tenant_can_never_publish_a_prior(owner_engine: AsyncEngine) -> None:
    """FINDING-P17-09 — §27's floors, and the pilot's real constraint.

    `MIN_CONTRIBUTORS` is 3. A single-merchant deployment withholds every cell
    however much data it gathers, so the planner keeps declining. This is
    k-anonymity working, and it is pinned here because it determines whether a
    one-tenant pilot can function at all.
    """
    async with owner_engine.begin() as conn:
        await _wipe(conn)
        await _tenant(conn, "t_solo", events=80)

    published, withheld = await aggregator_tick(owner_engine)
    assert published == 0
    assert withheld > 0

    async with owner_engine.begin() as conn:
        cells = (await conn.execute(text("SELECT count(*) c FROM segment_priors"))).one().c
    assert cells == 0


@pytest.mark.asyncio
async def test_three_tenants_clear_the_anonymity_floor(owner_engine: AsyncEngine) -> None:
    """The same data across three contributors publishes.

    Asserted alongside the single-tenant case so that "withheld" is shown to be
    about the floors rather than about the aggregator being broken.
    """
    async with owner_engine.begin() as conn:
        await _wipe(conn)
        for name in ("t_a", "t_b", "t_c"):
            await _tenant(conn, name, events=60)

    published, _ = await aggregator_tick(owner_engine)
    assert published > 0

    async with owner_engine.begin() as conn:
        row = (
            await conn.execute(text("SELECT count(*) c, min(n_obs) n FROM segment_priors"))
        ).one()
    assert row.c == published
    assert row.n >= 50, "a published cell must clear MIN_COHORT"
