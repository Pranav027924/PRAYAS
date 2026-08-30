"""Outcomes that arrive later (Master Spec §9, §19, §31; ADR-083).

Every rail before eNACH resolved when it fired. §9 says why that had to stop:
"Batch semantics mean 'attempt at 09:10 on the 5th' is meaningless; you present
into a clearing cycle and learn the outcome T+1. The rail adapter must express
budget, windows, and *outcome latency*, which a UPI-only design would never
surface."

So an attempt on a batch rail has three fates, not two:

* **resolved** — an outcome arrived and the attempt succeeded or failed;
* **awaiting** — presented, outcome not yet due, and *nothing may be inferred
  from the silence*;
* **overdue** — the outcome is past due and still absent, which is an
  operational alert rather than a failure.

**Silence is not failure, and this is the whole point.** The tempting bug is to
treat an unresolved attempt as failed and schedule a retry. On a rail with a
one-working-day latency that would present the same debit twice into successive
clearing cycles — taking the customer's money twice on a rail where reversal is
slow and manual. `retry_is_blocked` exists so the sequencer cannot make that
mistake by omission.

**Overdue is not failure either.** §19 puts an unreachable rail under
degradation, not under "assume the worst". An outcome that has not arrived is
unknown, and a system that resolved unknowns in the direction of "try again" is
a system that double-debits during an NPCI incident.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.domain.rails import adapter_for
from prayas.domain.states import AttemptState

#: How long past `outcome_due_at` an attempt may sit before it is an alert.
#: Generous, because clearing files are late for ordinary reasons and paging on
#: every one of those is how an alert stops being read.
OVERDUE_GRACE_HOURS: Final = 6

AWAITING: Final = "awaiting"
OVERDUE: Final = "overdue"
RESOLVED: Final = "resolved"


@dataclass(frozen=True, slots=True)
class OutcomeStatus:
    """Where one attempt stands, when the answer may simply not exist yet."""

    attempt_id: str
    rail: str
    state: str
    presented_at: datetime | None
    outcome_due_at: datetime | None

    @property
    def is_resolved(self) -> bool:
        return self.state in {AttemptState.SUCCEEDED.value, AttemptState.FAILED.value}

    def status_at(self, now: datetime) -> str:
        if self.is_resolved:
            return RESOLVED
        if self.outcome_due_at is None:
            # A real-time rail that has not resolved is not "awaiting" — there
            # is no latency to wait out. It is the executor's own problem.
            return OVERDUE
        return AWAITING if now < self.outcome_due_at else OVERDUE


async def mark_presented(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    attempt_id: str,
    rail: str,
    presented_at: datetime,
) -> datetime | None:
    """Record a presentation and when its outcome is due.

    Returns the due time, or None on a rail that resolves immediately. Writing
    `presented_at` on a real-time rail would imply a distinction that does not
    exist there.
    """
    adapter = adapter_for(rail)
    if adapter.outcome_latency.total_seconds() <= 0:
        return None

    due = adapter.outcome_due_at(presented_at)
    await conn.execute(
        text(
            "UPDATE attempts SET presented_at = :at, outcome_due_at = :due"
            " WHERE tenant_id = :t AND attempt_id = :id"
        ),
        {"at": presented_at, "due": due, "t": tenant_id, "id": attempt_id},
    )
    return due


async def awaiting_outcome(
    conn: AsyncConnection, tenant_id: str, *, now: datetime
) -> list[OutcomeStatus]:
    """Attempts presented into a clearing cycle whose outcome is not yet due."""
    return [s for s in await _presented(conn, tenant_id) if s.status_at(now) == AWAITING]


async def overdue_outcomes(
    conn: AsyncConnection, tenant_id: str, *, now: datetime, grace_hours: int = OVERDUE_GRACE_HOURS
) -> list[OutcomeStatus]:
    """Presented attempts whose outcome is late enough to be an alert.

    §19 calls this degradation. It is **not** licence to assume failure.
    """
    cutoff = now - timedelta(hours=grace_hours)
    return [
        s
        for s in await _presented(conn, tenant_id)
        if not s.is_resolved and s.outcome_due_at is not None and s.outcome_due_at < cutoff
    ]


async def _presented(conn: AsyncConnection, tenant_id: str) -> list[OutcomeStatus]:
    rows = await conn.execute(
        text(
            "SELECT a.attempt_id, a.state, a.presented_at, a.outcome_due_at, m.rail"
            " FROM attempts a"
            " JOIN cycles c ON c.cycle_id = a.cycle_id"
            " JOIN mandates m ON m.mandate_id = c.mandate_id"
            " WHERE a.tenant_id = :t AND a.outcome_due_at IS NOT NULL"
        ),
        {"t": tenant_id},
    )
    return [
        OutcomeStatus(
            attempt_id=row._mapping["attempt_id"],
            rail=row._mapping["rail"],
            state=row._mapping["state"],
            presented_at=row._mapping["presented_at"],
            outcome_due_at=row._mapping["outcome_due_at"],
        )
        for row in rows
    ]


def retry_is_blocked(status: OutcomeStatus, now: datetime) -> bool:
    """May a retry be scheduled for this attempt's cycle?

    **False only when the outcome is actually known.** An unresolved
    presentation blocks a retry whether it is merely awaiting or genuinely
    overdue, because in both cases the money may already have moved. Presenting
    again into the next clearing cycle would debit the customer twice on a rail
    where reversal is slow and manual.

    This is the function that stops the executor inferring failure from
    silence, and it errs toward not acting — the direction where the cost is
    delay rather than a double debit.
    """
    del now
    return not status.is_resolved
