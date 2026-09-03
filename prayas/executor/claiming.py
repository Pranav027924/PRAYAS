"""Durable timer claiming and lease reclamation (Playbook Phase 6; ADR-044).

`FOR UPDATE SKIP LOCKED` lets many workers drain one queue without blocking
each other: a row another worker holds is skipped rather than waited on.

The lease is `scheduled_actions.locked_until`. A worker that dies leaves its
claim behind with a lease in the future; once that passes, the row is
reclaimable. There is no worker identity column and none is needed — expiry is
the only signal reclamation requires, and adding one would be a schema change
for no behavioural gain.

**Claiming is per tenant.** RLS scopes every query to the bound tenant, so the
worker drains one tenant at a time. That is also what §18 asks for: "decision
work is drained with weighted fair queuing keyed on tenant, so one merchant's
month-start burst cannot starve another's."
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from prayas.db.tenancy import system_transaction

log = logging.getLogger(__name__)

#: ADR-044. Far above any plausible fire-transaction time, so a slow-but-alive
#: worker never has its claim stolen; short enough that a crash returns timers
#: within a minute.
LEASE_SECONDS: Final = 60
POLL_INTERVAL_SECONDS: Final = 1.0
CLAIM_BATCH: Final = 50

STATE_PENDING: Final = "pending"
STATE_CLAIMED: Final = "claimed"
STATE_DONE: Final = "done"
STATE_CANCELLED: Final = "cancelled"
STATE_ABANDONED: Final = "abandoned"


@dataclass(frozen=True, slots=True)
class ClaimedAction:
    action_id: str
    tenant_id: str
    cycle_id: str | None
    mandate_id: str | None
    action_type: str
    fire_at: datetime
    payload: dict[str, Any]


_CLAIM_SQL: Final = text(
    """
    UPDATE scheduled_actions
       SET state = :claimed,
           locked_until = now() + make_interval(secs => :lease)
     WHERE action_id IN (
           SELECT action_id
             FROM scheduled_actions
            WHERE tenant_id = :tenant_id
              AND fire_at <= :now
              AND state IN (:pending, :claimed)
              AND (locked_until IS NULL OR locked_until < now())
            ORDER BY fire_at
            FOR UPDATE SKIP LOCKED
            LIMIT :batch
       )
    RETURNING action_id, tenant_id, cycle_id, mandate_id, action_type, fire_at, payload
    """
)


async def claim_due_actions(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    batch: int = CLAIM_BATCH,
    lease_seconds: int = LEASE_SECONDS,
    now: datetime | None = None,
) -> list[ClaimedAction]:
    """Claim up to `batch` due actions for this tenant.

    One statement, so claiming is atomic: the inner `SELECT ... FOR UPDATE SKIP
    LOCKED` picks rows no other worker holds, and the outer `UPDATE` stamps the
    lease before anyone else can see them.

    `state IN (pending, claimed)` with an expired lease is what reclaims a dead
    worker's rows — a claim whose lease has passed is indistinguishable from an
    unclaimed one, which is exactly the intent.

    `now` is what counts as *due* — separate from what fire-time revalidation
    later counts as *legal* (`fire_action`'s own `now`). Defaults to the
    application clock, computed once here and bound as a parameter rather than
    read from the database's `now()`, so a caller can pin it — a test, or Demo
    spec Phase 6's per-tenant virtual clock — through the same argument
    production leaves at its default.
    """
    result = await conn.execute(
        _CLAIM_SQL,
        {
            "tenant_id": tenant_id,
            "claimed": STATE_CLAIMED,
            "pending": STATE_PENDING,
            "lease": lease_seconds,
            "batch": batch,
            "now": now or datetime.now(UTC),
        },
    )
    return [
        ClaimedAction(
            action_id=row.action_id,
            tenant_id=row.tenant_id,
            cycle_id=row.cycle_id,
            mandate_id=row.mandate_id,
            action_type=row.action_type,
            fire_at=row.fire_at,
            payload=row.payload if isinstance(row.payload, dict) else {},
        )
        for row in result
    ]


async def release_action(
    conn: AsyncConnection, action_id: str, tenant_id: str, *, state: str
) -> None:
    """Settle a claim terminally, clearing the lease."""
    await conn.execute(
        text(
            "UPDATE scheduled_actions SET state = :state, locked_until = NULL"
            " WHERE action_id = :action_id AND tenant_id = :tenant_id"
        ),
        {"state": state, "action_id": action_id, "tenant_id": tenant_id},
    )


async def active_tenants(engine: AsyncEngine) -> list[str]:
    """Tenants to drain, in a stable order.

    Goes through `prayas_tenant_ids()` rather than reading `tenants` directly
    (ADR-046). `tenants` is under RLS keyed on `app.tenant_id`, so a worker
    with no tenant bound sees nothing — it cannot discover which tenants to
    bind. The function is SECURITY DEFINER and returns only the id column, so
    `config` stays behind the policy.
    """
    async with system_transaction(engine) as conn:
        result = await conn.execute(text("SELECT tenant_id FROM prayas_tenant_ids()"))
        return [row.tenant_id for row in result]


async def cancel_actions_for_cycle(conn: AsyncConnection, tenant_id: str, cycle_id: str) -> int:
    """Cancel every pending action for a cycle. Returns rows affected.

    The late-capture guard (Phase 1) uses this: once a cycle is paid, nothing
    scheduled against it may fire. Deliberately does not touch rows already
    `done` — cancelling history would rewrite what happened.
    """
    result = await conn.execute(
        text(
            "UPDATE scheduled_actions SET state = :cancelled, locked_until = NULL"
            " WHERE tenant_id = :tenant_id AND cycle_id = :cycle_id"
            "   AND state IN (:pending, :claimed)"
        ),
        {
            "cancelled": STATE_CANCELLED,
            "tenant_id": tenant_id,
            "cycle_id": cycle_id,
            "pending": STATE_PENDING,
            "claimed": STATE_CLAIMED,
        },
    )
    return result.rowcount or 0
