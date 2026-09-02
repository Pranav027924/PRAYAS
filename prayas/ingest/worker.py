"""Projector service — drains `events_raw` into domain state (§17.2, §43).

The ingest handler acks fast and persists the raw event; §17.2's flow then runs
`ingest → projector → decision-svc`. Nothing was running that middle step, so
events accumulated in `events_raw` and no state ever moved (FINDING-P17-04).
This is the process that runs it.

All the work lives in `projector.project_tenant`, which is already transactional
and already claims with `FOR UPDATE SKIP LOCKED`. This module is the loop around
it and nothing more — deliberately, because the interesting correctness (the
watermark advancing in the same transaction as the projection) belongs there.

Separate from the executor for the same reason the executor is separate from
the API (ADR-042): projection is a read-heavy fold over history whose cost
scales with a mandate's event count, and it must not compete with the fire path
for a connection pool or a CPU. Several replicas are safe — `SKIP LOCKED` means
they take disjoint batches.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.config import Settings
from prayas.db.engine import create_app_engine
from prayas.executor.claiming import active_tenants
from prayas.ingest.projector import consumer_lag, project_tenant
from prayas.observability import metrics
from prayas.observability.logging import configure

log = logging.getLogger(__name__)

#: How long to wait when a pass found nothing. §43 alerts on projector lag
#: above 30s, so the idle poll must stay well under it.
POLL_INTERVAL_SECONDS = 5.0

#: Events per tenant per pass. Matches `project_tenant`'s own default.
BATCH = 200

#: Passes to make over a tenant that keeps returning full batches. Bounded so
#: one tenant with a large backlog cannot starve every other tenant — the same
#: round-robin fairness the executor's claim loop provides.
MAX_PASSES_PER_TENANT = 5


@dataclass(frozen=True, slots=True)
class TickResult:
    projected: int
    tenants: int


async def drain_tenant(engine: AsyncEngine, tenant_id: str, *, batch: int = BATCH) -> int:
    """Project up to `MAX_PASSES_PER_TENANT` batches for one tenant."""
    total = 0
    for _ in range(MAX_PASSES_PER_TENANT):
        consumed = await project_tenant(engine, tenant_id, batch_size=batch)
        total += consumed
        if consumed < batch:
            break  # drained

    # Reported even when nothing was consumed: a lag that stays high with zero
    # throughput is the signal worth alerting on, and it is invisible if the
    # gauge is only written on the happy path.
    await consumer_lag(engine, tenant_id)
    return total


async def tick(engine: AsyncEngine, *, batch: int = BATCH) -> TickResult:
    """One pass over every tenant."""
    tenants = await active_tenants(engine)
    projected = 0
    for tenant_id in tenants:
        try:
            projected += await drain_tenant(engine, tenant_id, batch=batch)
        except Exception:
            # One tenant's failure must not stop the others. Projection is
            # idempotent — it recomputes from full history — so the batch is
            # simply retried on the next tick.
            metrics.increment("projector_tenant_error", tenant_id=tenant_id)
            log.exception("projector.tenant_failed", extra={"tenant_id": tenant_id})
    return TickResult(projected=projected, tenants=len(tenants))


async def run(
    engine: AsyncEngine,
    *,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    stop: asyncio.Event | None = None,
) -> None:
    """Poll until stopped.

    A failing tick is logged and the loop continues. A dead projector is a
    silent failure: webhooks keep returning 200 and nothing downstream ever
    happens, which is precisely how FINDING-P17-04 stayed invisible.
    """
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            result = await tick(engine)
            if result.projected:
                log.info(
                    "projector.tick",
                    extra={"projected": result.projected, "tenants": result.tenants},
                )
        except Exception:
            metrics.increment("projector_tick_error")
            log.exception("projector.tick_failed")

        with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)


async def main() -> None:  # pragma: no cover - process entry point
    """Service entry point for the `projector` Compose service."""
    settings = Settings.from_env()
    configure(settings.log_level)

    engine = create_app_engine(settings.database_url_app)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    log.info("projector.starting", extra={"env": settings.env})
    try:
        await run(engine, stop=stop)
    finally:
        await engine.dispose()
        log.info("projector.stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
