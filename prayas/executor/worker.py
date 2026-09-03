"""The executor worker loop (ADR-042; Playbook Phase 6).

Runs as its own service so it scales on pending-timer depth (§15) and so it can
be killed independently - which two Phase 6 exit criteria require, and which a
thread inside the API could not satisfy honestly.

**Drains per tenant.** RLS scopes every query to the bound tenant, so the loop
binds `app.tenant_id` for each in turn. That is also §18's design: "decision
work is drained with weighted fair queuing keyed on tenant, so one merchant's
month-start burst cannot starve another's." Ordering tenants stably and
claiming a bounded batch from each gives round-robin fairness; true weighting
arrives with per-tenant quotas.

Each action fires in its own transaction, separate from the claim. That is
load-bearing for **lock ordering**, not only for crash granularity: claiming
locks `scheduled_actions`, while firing locks `cycles` and then
`scheduled_actions`. Doing both in one transaction would take them
actions-first, whereas the late-capture guard takes them cycle-first - opposite
orders, which deadlocks under exactly the race Phase 6's adversarial test
covers. Splitting them makes every path lock the cycle first.

One tenant's failure also cannot roll back another's committed work, and a
crash loses at most the action in flight - whose lease expires and returns it
to the pool.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.config import Settings
from prayas.db.engine import create_app_engine
from prayas.db.tenancy import tenant_transaction
from prayas.demo.clock import current_now
from prayas.executor.claiming import (
    CLAIM_BATCH,
    LEASE_SECONDS,
    POLL_INTERVAL_SECONDS,
    active_tenants,
    claim_due_actions,
)
from prayas.executor.firing import fire_action
from prayas.executor.notice import fire_notice, is_notice
from prayas.executor.outbox import relay_once
from prayas.executor.provider import RailProvider
from prayas.observability import metrics
from prayas.observability.logging import configure

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TickResult:
    claimed: int
    fired: int
    refused: int
    relayed: int


async def drain_tenant(
    engine: AsyncEngine,
    tenant_id: str,
    provider: RailProvider,
    *,
    batch: int = CLAIM_BATCH,
    lease_seconds: int = LEASE_SECONDS,
    now: datetime | None = None,
) -> TickResult:
    """Claim and fire one tenant's due actions, then relay their intents.

    Claiming and firing are answered from *two independently resolved*
    clocks, on purpose (restoring, not just repeating, what the pre-Phase-6
    code did — see FINDING-P17-19 below).

    **What is due** — `claim_due_actions`'s own `now` — is always this
    tenant's own current instant (`current_now`): the wall clock, plus a demo
    tenant's stored advance (Demo spec Phase 6). Never overridden by the
    `now` parameter below, no matter what a caller passes it for.

    **What is legal** — the instant fire-time revalidation and the gate
    evaluate against — is `now` when a caller supplies one (tests pin it, so
    the gate's NPCI window check doesn't pass or fail on the hour the suite
    happens to run), and otherwise the same `current_now` claiming just used.

    FINDING-P17-19: collapsing these to one clock broke the seeder's two-pass
    settle — a notice pinned to a past simulated hour failed to claim under
    that clock, so the *debit* pass's later, future-pointing simulated hour
    claimed the still-pending notice too and fired it at the debit's own
    instant. `pdn_sent_at` landed equal to `fired_at`, `hours_since_pdn`
    computed to zero, and every debit was denied on `RBI-EMANDATE-PDN-24H` —
    100% of them, in a run that previously allowed the majority. The fix is
    the one-line separation above: claiming was never supposed to move when a
    caller substitutes a different instant for the *gate's* reasoning.
    """
    async with tenant_transaction(engine, tenant_id) as conn:
        claim_now = await current_now(conn, tenant_id)
        actions = await claim_due_actions(
            conn, tenant_id, batch=batch, lease_seconds=lease_seconds, now=claim_now
        )

    fire_now = now if now is not None else claim_now
    fired = refused = 0
    for action in actions:
        # One transaction per action: a refusal for one must not unwind
        # another's committed decision record.
        async with tenant_transaction(engine, tenant_id) as conn:
            if is_notice(action):
                # A notice never reaches the rail and never spends attempt
                # budget (ADR-099). Routing it through `fire_action` would do
                # both, charging §1's retry allowance for a message.
                notice = await fire_notice(conn, action, now=fire_now)
                fired += 1 if notice.sent else 0
                refused += 0 if notice.sent else 1
                continue
            outcome = await fire_action(conn, action, now=fire_now)
        if outcome.fired:
            fired += 1
        else:
            refused += 1

    relay = await relay_once(engine, tenant_id, provider)
    return TickResult(claimed=len(actions), fired=fired, refused=refused, relayed=relay.sent)


async def tick(
    engine: AsyncEngine,
    provider: RailProvider,
    *,
    batch: int = CLAIM_BATCH,
    now: datetime | None = None,
) -> TickResult:
    """One pass over every tenant."""
    totals = [0, 0, 0, 0]
    for tenant_id in await active_tenants(engine):
        result = await drain_tenant(engine, tenant_id, provider, batch=batch, now=now)
        totals[0] += result.claimed
        totals[1] += result.fired
        totals[2] += result.refused
        totals[3] += result.relayed
    return TickResult(*totals)


async def run(
    engine: AsyncEngine,
    provider: RailProvider,
    *,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    stop: asyncio.Event | None = None,
    now: datetime | None = None,
) -> None:
    """Poll until stopped.

    An exception in one tick is logged and the loop continues: a transient
    database error must not take the executor down, because a dead executor
    fires nothing and that failure is silent.
    """
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            result = await tick(engine, provider, now=now)
            if result.claimed:
                log.info(
                    "executor.tick",
                    extra={
                        "claimed": result.claimed,
                        "fired": result.fired,
                        "refused": result.refused,
                        "relayed": result.relayed,
                    },
                )
        except Exception:
            metrics.increment("executor_tick_error")
            log.exception("executor.tick_failed")

        with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)


async def main() -> None:  # pragma: no cover - process entry point
    """Service entry point for the `executor` Compose service."""
    settings = Settings.from_env()
    configure(settings.log_level)

    from prayas.executor.provider import FakeProvider

    engine = create_app_engine(settings.database_url_app)
    # Phase 17 substitutes the real Razorpay adapter here (ADR-043). Until then
    # the executor must not reach an external service.
    provider: RailProvider = FakeProvider()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    log.info("executor.starting", extra={"env": settings.env})
    try:
        await run(engine, provider, stop=stop)
    finally:
        await engine.dispose()
        log.info("executor.stopped")


def _entrypoint(*_: Any) -> None:  # pragma: no cover
    asyncio.run(main())


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
