"""Event log → domain state (Playbook Phase 1; Master Spec §11, §17.2).

**How convergence is achieved.** The exit criterion is that *any* permutation of
an event set produces identical state. That is not obtained by making increments
commutative and hoping; it is structural:

* Cycle facts are **set aggregations** over every event for that cycle —
  `attempts_used` is the count of *distinct failure event ids*, never an
  increment. A set has no order, so no ordering can change the answer.
* Mandate state is a fold over events sorted by `(occurred_at, event_id)`, a
  total order independent of arrival order.
* Terminal states absorb (§11), so a late event cannot reanimate a settled cycle.

The projector recomputes an entity from its full history rather than mutating it
incrementally. That costs reads and would want snapshots at scale; correctness
first, and the trade is recorded for Phase 15.

Concurrency: the mandate row is locked `FOR UPDATE` before its cycles are
written, so two projector workers cannot interleave on one mandate. `version`
still increments for the benefit of optimistic readers downstream.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from prayas.db.tenancy import tenant_transaction
from prayas.domain.states import (
    CYCLE_PAID,
    MANDATE_TERMINAL,
    CycleState,
    MandateState,
    can_transition_mandate,
)
from prayas.ingest.envelope import (
    Event,
    extract_amount_paise,
    extract_cycle_ref,
    extract_next_billing_at,
)
from prayas.observability import metrics

log = logging.getLogger(__name__)

#: §9 — UPI Autopay permits 1 execution + up to 3 retries. Other rails: Phase 14.
RAIL_ATTEMPT_BUDGET: Final[dict[str, int]] = {"upi_autopay": 4}
DEFAULT_ATTEMPT_BUDGET: Final = 4

#: §11 MANDATE state machine, keyed by the events that drive it.
MANDATE_EVENTS: Final[dict[str, MandateState]] = {
    "subscription.authenticated": MandateState.ACTIVE,
    "subscription.activated": MandateState.ACTIVE,
    "subscription.resumed": MandateState.ACTIVE,
    "subscription.paused": MandateState.PAUSED,
    "subscription.halted": MandateState.AT_RISK,
    "subscription.cancelled": MandateState.REVOKED,
    "subscription.completed": MandateState.EXPIRED,
}

#: A failed debit consumes one unit of the regulator's budget.
FAILURE_EVENTS: Final[frozenset[str]] = frozenset({"payment.failed"})

#: Money arrived. `payment.captured` after `payment.failed` is the late capture.
SUCCESS_EVENTS: Final[frozenset[str]] = frozenset(
    {"payment.captured", "subscription.charged", "invoice.paid"}
)


@dataclass(frozen=True, slots=True)
class CycleProjection:
    state: CycleState
    attempts_used: int
    last_failure_at: datetime | None
    recovered_paise: int


def project_cycle(events: Sequence[Event]) -> CycleProjection:
    """Fold a cycle's events into state. Order-independent by construction."""
    # Keyed by event_id: a duplicate delivery collapses to one fact, so
    # attempts_used counts distinct failures rather than deliveries.
    failures = {e.event_id: e for e in events if e.event_type in FAILURE_EVENTS}
    successes = {e.event_id: e for e in events if e.event_type in SUCCESS_EVENTS}

    attempts_used = len(failures)
    last_failure_at = max((e.occurred_at for e in failures.values()), default=None)

    if successes:
        # Earliest success under the total order wins, so which payment is
        # credited never depends on delivery order.
        winner = min(successes.values(), key=lambda e: e.ordering_key)
        amount = extract_amount_paise(winner.payload) or 0
        return CycleProjection(CycleState.SUCCEEDED, attempts_used, last_failure_at, amount)

    if failures:
        return CycleProjection(CycleState.EXECUTING, attempts_used, last_failure_at, 0)

    return CycleProjection(CycleState.SCHEDULED, 0, None, 0)


def project_mandate(events: Sequence[Event]) -> MandateState:
    """Fold mandate events in total order, rejecting illegal transitions.

    Rejections are counted rather than raised: an out-of-order webhook is an
    expected condition, and `stale_transition` is how it becomes visible
    (Playbook Phase 1).
    """
    state = MandateState.CREATED

    for event in sorted(events, key=lambda e: e.ordering_key):
        proposed = MANDATE_EVENTS.get(event.event_type)
        if proposed is None or proposed == state:
            continue

        if state in MANDATE_TERMINAL:
            metrics.increment(
                "stale_transition",
                entity="mandate",
                reason="terminal_absorbed",
                from_state=str(state),
                to_state=str(proposed),
            )
        elif can_transition_mandate(state, proposed):
            state = proposed
        else:
            metrics.increment(
                "stale_transition",
                entity="mandate",
                reason="illegal_transition",
                from_state=str(state),
                to_state=str(proposed),
            )

    return state


def _row_to_event(row: Any, tenant_id: str) -> Event:
    payload: dict[str, Any] = (
        row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    )
    created_at = payload.get("created_at")
    occurred_at = (
        datetime.fromtimestamp(created_at, tz=UTC)
        if isinstance(created_at, int)
        else row.received_at
    )
    return Event(
        event_id=row.event_id,
        tenant_id=tenant_id,
        event_type=row.event_type,
        mandate_id=row.mandate_id,
        cycle_ref=extract_cycle_ref(payload),
        occurred_at=occurred_at,
        payload=payload,
    )


async def _events_for_mandate(
    conn: AsyncConnection, tenant_id: str, mandate_id: str
) -> list[Event]:
    """Every verified event for this mandate — the fold needs full history."""
    result = await conn.execute(
        text(
            "SELECT event_id, event_type, mandate_id, payload, received_at"
            " FROM events_raw"
            " WHERE tenant_id = :tenant_id AND mandate_id = :mandate_id AND signature_ok"
        ),
        {"tenant_id": tenant_id, "mandate_id": mandate_id},
    )
    return [_row_to_event(row, tenant_id) for row in result]


async def _lock_mandate(conn: AsyncConnection, tenant_id: str, mandate_id: str) -> Any | None:
    """Serialise projection per mandate. Returns the row, or None if absent."""
    result = await conn.execute(
        text(
            "SELECT mandate_id, rail, state, version FROM mandates"
            " WHERE tenant_id = :tenant_id AND mandate_id = :mandate_id"
            " FOR UPDATE"
        ),
        {"tenant_id": tenant_id, "mandate_id": mandate_id},
    )
    return result.one_or_none()


async def _apply_mandate(
    conn: AsyncConnection, tenant_id: str, mandate_id: str, state: MandateState
) -> None:
    await conn.execute(
        text(
            "UPDATE mandates SET state = :state, version = version + 1"
            " WHERE tenant_id = :tenant_id AND mandate_id = :mandate_id"
            "   AND state IS DISTINCT FROM :state"
        ),
        {"tenant_id": tenant_id, "mandate_id": mandate_id, "state": str(state)},
    )


async def _upsert_cycle(
    conn: AsyncConnection,
    tenant_id: str,
    cycle_id: str,
    mandate_id: str,
    rail: str,
    events: Sequence[Event],
    projection: CycleProjection,
) -> None:
    """Write the cycle, creating it on first sight.

    `deadline_at` is the next billing date (ADR-018) — §1's "one execution plus
    up to three retries per cycle, then the cycle is over" means the cycle
    boundary and the budget describe the same thing.
    """
    ordered = sorted(events, key=lambda e: e.ordering_key)
    due_at = ordered[0].occurred_at
    deadline_at = next(
        (
            billing
            for billing in (extract_next_billing_at(e.payload) for e in ordered)
            if billing is not None
        ),
        None,
    )

    params = {
        "tenant_id": tenant_id,
        "cycle_id": cycle_id,
        "mandate_id": mandate_id,
        "amount_paise": next(
            (a for a in (extract_amount_paise(e.payload) for e in ordered) if a), 0
        ),
        "due_at": due_at,
        "deadline_at": deadline_at,
        "attempt_budget": RAIL_ATTEMPT_BUDGET.get(rail, DEFAULT_ATTEMPT_BUDGET),
        "state": str(projection.state),
        "attempts_used": projection.attempts_used,
        "last_failure_at": projection.last_failure_at,
        "recovered_paise": projection.recovered_paise,
    }

    if params["deadline_at"] is None:
        # No provider billing date yet. Record it and skip rather than invent a
        # deadline: §23.4 stops a cycle on `now() > deadline_at`, so a wrong
        # value here stops collection at the wrong time.
        metrics.increment("cycle_deadline_unresolved", cycle_id=cycle_id, mandate_id=mandate_id)
        return

    await conn.execute(
        text(
            "INSERT INTO cycles (cycle_id, tenant_id, mandate_id, seq_no, amount_paise,"
            "   due_at, deadline_at, attempt_budget, attempts_used, state,"
            "   last_failure_at, recovered_paise)"
            " VALUES (:cycle_id, :tenant_id, :mandate_id, 1, :amount_paise,"
            "   :due_at, :deadline_at, :attempt_budget, :attempts_used, :state,"
            "   :last_failure_at, :recovered_paise)"
            " ON CONFLICT (cycle_id) DO UPDATE SET"
            "   state = EXCLUDED.state,"
            "   attempts_used = EXCLUDED.attempts_used,"
            "   last_failure_at = EXCLUDED.last_failure_at,"
            "   recovered_paise = EXCLUDED.recovered_paise,"
            "   version = cycles.version + 1"
        ),
        params,
    )


async def _cancel_pending_actions(conn: AsyncConnection, tenant_id: str, cycle_id: str) -> int:
    """Late-capture guard: a settled-and-paid cycle has nothing left to do.

    Nothing schedules actions until Phase 6; this cancels whatever is pending so
    the guard is already correct the moment something does.
    """
    result = await conn.execute(
        text(
            "UPDATE scheduled_actions SET state = 'cancelled'"
            " WHERE tenant_id = :tenant_id AND cycle_id = :cycle_id AND state = 'pending'"
        ),
        {"tenant_id": tenant_id, "cycle_id": cycle_id},
    )
    return result.rowcount or 0


async def _project_one_mandate(conn: AsyncConnection, tenant_id: str, mandate_id: str) -> None:
    mandate = await _lock_mandate(conn, tenant_id, mandate_id)
    if mandate is None:
        # An event for a mandate we have never seen. Recorded, not dropped —
        # events_raw deliberately has no FK (ADR-008) so evidence survives.
        metrics.increment("stale_transition", entity="mandate", reason="unknown_mandate")
        return

    events = await _events_for_mandate(conn, tenant_id, mandate_id)
    if not events:
        return

    await _apply_mandate(conn, tenant_id, mandate_id, project_mandate(events))

    by_cycle: dict[str, list[Event]] = {}
    for event in events:
        if event.cycle_ref:
            by_cycle.setdefault(event.cycle_ref, []).append(event)

    for cycle_ref, cycle_events in sorted(by_cycle.items()):
        projection = project_cycle(cycle_events)
        await _upsert_cycle(
            conn, tenant_id, cycle_ref, mandate_id, mandate.rail, cycle_events, projection
        )

        if projection.state in CYCLE_PAID:
            cancelled = await _cancel_pending_actions(conn, tenant_id, cycle_ref)
            if cancelled:
                log.info(
                    "projector.late_capture_cancelled",
                    extra={"cycle_id": cycle_ref, "cancelled": cancelled},
                )


async def project_tenant(engine: AsyncEngine, tenant_id: str, *, batch_size: int = 200) -> int:
    """Project one batch of unprocessed events. Returns how many were consumed.

    Log and projected state move in one transaction (ADR-015), so an event
    cannot be stamped processed while its projection rolls back.
    """
    async with tenant_transaction(engine, tenant_id) as conn:
        claimed = await conn.execute(
            text(
                "SELECT event_id, mandate_id FROM events_raw"
                " WHERE tenant_id = :tenant_id AND processed_at IS NULL AND signature_ok"
                " ORDER BY received_at"
                " FOR UPDATE SKIP LOCKED"
                " LIMIT :batch_size"
            ),
            {"tenant_id": tenant_id, "batch_size": batch_size},
        )
        rows = list(claimed)
        if not rows:
            return 0

        for mandate_id in sorted({r.mandate_id for r in rows if r.mandate_id}):
            await _project_one_mandate(conn, tenant_id, mandate_id)

        await conn.execute(
            text(
                "UPDATE events_raw SET processed_at = now()"
                " WHERE tenant_id = :tenant_id AND event_id = ANY(:event_ids)"
            ),
            {"tenant_id": tenant_id, "event_ids": [r.event_id for r in rows]},
        )

    return len(rows)


async def consumer_lag(engine: AsyncEngine, tenant_id: str) -> float:
    """§43 — projector consumer lag, alert threshold 30s."""
    async with tenant_transaction(engine, tenant_id) as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT count(*) AS pending,"
                    " COALESCE(EXTRACT(EPOCH FROM (now() - min(received_at))), 0) AS lag"
                    " FROM events_raw"
                    " WHERE tenant_id = :tenant_id AND processed_at IS NULL"
                ),
                {"tenant_id": tenant_id},
            )
        ).one()

    lag = float(row.lag)
    metrics.projector_lag(lag, pending=int(row.pending))
    return lag
