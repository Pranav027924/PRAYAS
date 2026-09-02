"""Decision service — turns projected state into scheduled actions (ADR-096).

§17.2's flow is `ingest → projector → decision-svc → executor → ledger`. The
projector was missing and is now `prayas/ingest/worker.py`; this is the other
half. Before it, `INSERT INTO scheduled_actions` existed only in tests, so the
executor drained a queue nothing filled (FINDING-P17-04).

**What it does not do is as important as what it does.** It schedules; it does
not fire, and it does not decide whether firing is lawful. §32 requires the
gate to be re-evaluated at *fire* time with `as_of = NOW()`, because rules and
state both move between scheduling and firing. A gate check here would be a
second, staler opinion, and the temptation to trust it instead is exactly how
invariant 3 gets broken. The executor re-reads everything.

**Three guards before a cycle is even considered:**

1. `may_decide(stage)` — §44's OBSERVE ingests only.
2. `may_act_on(stage, ...)` — the stage's mandate share (ADR-095). A CANARY
   that schedules for the whole portfolio is not a canary, and a holdout that
   gets scheduled is not a holdout.
3. No action already pending or claimed for the cycle. Without this the planner
   would queue a second attempt every tick.

**Stopping is an output.** §23.3 lets the DP decline to attempt at all when no
legal slot carries positive expected value. `best_slot` returning `None` is
that answer, and it is recorded rather than treated as a failure to plan.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from prayas.adoption.cohort import Arm, arm_for
from prayas.adoption.stages import may_decide, may_fire
from prayas.adoption.store import current_stage
from prayas.config import Settings
from prayas.db.engine import create_app_engine
from prayas.db.tenancy import system_transaction, tenant_transaction
from prayas.domain.rails import PDN_CUTOFF_HOUR_IST, adapter_for
from prayas.executor.claiming import STATE_CLAIMED, STATE_PENDING, active_tenants
from prayas.executor.notice import ACTION_TYPE_NOTICE
from prayas.inference.bands import ticket_band
from prayas.inference.cause import infer, is_terminal
from prayas.models.live import PriorTable, load_priors, presence_curve
from prayas.notify.planner import (
    CONTACT_CLOSE_IST,
    MAX_NOTICE_HOURS,
    MIN_NOTICE_HOURS,
    is_contact_window,
)
from prayas.notify.planner import plan as notification_plan
from prayas.observability import metrics
from prayas.observability.logging import configure
from prayas.planner.datechange import maybe_propose as maybe_propose_date_change
from prayas.retention.revocation import RevocationFeatures, RevocationModel
from prayas.sequencer.dp import BoolArray, solve
from prayas.sequencer.economics import (
    attempt_cost_matrix,
    continuation_value_paise,
    health_multiplier,
    revocation_delta,
)
from prayas.sequencer.windows import DEFAULT_HORIZON_SLOTS, build_mask

log = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 15.0

#: §21's PDN lead, in hours. The DP will not choose a slot closer than this.
PDN_LEAD_HOURS = 24

#: Bootstrap monthly revocation hazard, used until §22's model is fitted on a
#: tenant's own lifecycles. Matches `SimConfig.revocation_beta`, the same value
#: ADR-037 chose and for the same reason: the DP then optimises against the
#: revocation process the labelled data actually exhibits.
#:
#: **Supplying a model at all is the point, not the value.** Without one,
#: `revocation_delta` returns ADR-037's flat `0.04` at *every* slot, so an
#: attempt costs `0.04 x 12A = 0.48A` in expected continuation value no matter
#: when it fires — and no realistic liquidity prior clears that, so the DP
#: declines everything. §22's `marginal_delta` is instead the risk accrued by
#: *waiting*, near zero for an early slot and rising with delay. ADR-062: that
#: time-dependence "is what finally gives the sequencer a reason to act early
#: rather than at the last legal slot".
BOOTSTRAP_MONTHLY_REVOCATION = 0.04

#: Cycles *acted on* per tenant per pass.
BATCH = 100

#: How many rows to read to find them. At CANARY the stage treats 1% of
#: mandates, so a page the size of the batch is ~99% cycles the planner must
#: skip — correct, but it makes progress crawl. Reading wider absorbs the
#: excluded rows in one query instead of one page per handful of treatable
#: cycles.
PAGE = BATCH * 12


@dataclass(frozen=True, slots=True)
class TickResult:
    considered: int
    scheduled: int
    stopped: int
    shadowed: int
    proposed: int = 0
    #: Rows read, whether acted on or not. The cursor advances by this so a
    #: run of un-actionable cycles cannot block the ones behind it.
    examined: int = 0


_CANDIDATES = text(
    "SELECT c.cycle_id, c.mandate_id, c.amount_paise, c.due_at, c.deadline_at,"
    "       c.attempt_budget, c.attempts_used, m.rail, m.mcc"
    "  FROM cycles c"
    "  JOIN mandates m ON m.tenant_id = c.tenant_id AND m.mandate_id = c.mandate_id"
    " WHERE c.tenant_id = :tenant_id"
    "   AND c.state = 'executing'"
    "   AND c.attempts_used < c.attempt_budget"
    "   AND c.deadline_at > :now"
    "   AND NOT EXISTS ("
    "       SELECT 1 FROM scheduled_actions a"
    "        WHERE a.tenant_id = c.tenant_id AND a.cycle_id = c.cycle_id"
    "          AND a.state IN (:pending, :claimed))"
    # A *total* order, and paged. `deadline_at` alone is not total — a fleet
    # billing on one schedule shares a single deadline, so the sort collapses
    # to an arbitrary but stable heap order and every tick reads the same
    # prefix. Any row the planner declines to act on (a holdout mandate, say)
    # then blocks that prefix permanently: the planner reads it, skips it,
    # and never reaches the cycles behind it. Observed live as a fleet with
    # 5,198 treatment cycles making zero progress behind 100 holdout ones.
    " ORDER BY c.deadline_at, c.cycle_id"
    " OFFSET :offset LIMIT :batch"
)


async def _latest_decline_code(conn: Any, tenant_id: str, mandate_id: str) -> str | None:
    """The most recent decline code seen for this mandate.

    Only the code the provider returned — never any inferred cause — so
    `p_recoverable` rests on an observable, matching the measured path.
    """
    row = (
        await conn.execute(
            text(
                "SELECT COALESCE("
                "   payload#>>'{payload,payment,entity,error_reason}',"
                "   payload#>>'{payload,payment,entity,error_code}') AS code"
                "  FROM events_raw"
                " WHERE tenant_id = :t AND mandate_id = :m AND signature_ok"
                "   AND event_type = 'payment.failed'"
                " ORDER BY received_at DESC LIMIT 1"
            ),
            {"t": tenant_id, "m": mandate_id},
        )
    ).first()
    return str(row.code) if row is not None and row.code is not None else None


def _recoverable_probability(decline_code: str | None) -> float:
    """Posterior mass on non-terminal causes (§22)."""
    posterior = infer(decline_code)
    return float(sum(p for cause, p in posterior.posterior.items() if not is_terminal(cause)))


def _bootstrap_revocation_model() -> RevocationModel:
    """A model with no fitted cells, so every query takes the global rate.

    `monthly_hazard` falls back to `global_hazard` below `MIN_CELL_OBSERVATIONS`,
    so an empty model is a legitimate, honest construction rather than a stub:
    it says "no cell has enough mandates to differ from the population", which
    is exactly true on a new tenant.
    """
    return RevocationModel(
        cell_hazard={}, cell_counts={}, global_hazard=BOOTSTRAP_MONTHLY_REVOCATION
    )


def _revocation_features(
    *, rail: str, attempts_used: int, days_since_success: float, successful_cycles: int
) -> RevocationFeatures:
    """§22's features, all read off projected state."""
    return RevocationFeatures(
        consecutive_failures=attempts_used,
        days_since_success=days_since_success,
        successful_cycles=successful_cycles,
        rail=rail,
    )


def _notice_feasible_mask(*, decided_at: datetime, horizon_slots: int) -> BoolArray:
    """Debit slots for which a lawful notice instant exists.

    §30.1 imposes two constraints that interact: the notice must lead the debit
    by at least 24 hours, **and** it must fall inside the contact window. A
    debit slot is only lawful if some instant satisfies both, and the two cannot
    be checked independently — at 00:30 IST every instant in the next 25 hours
    that clears the lead is outside the window, so the earliest lawful debit is
    much later than the lead alone implies.

    Computed as a sliding window rather than by asking the notice planner about
    every slot. That literal version was O(horizon x candidates) — roughly
    52,000 datetime comparisons per cycle — and it dominated planning time to
    the point that a fleet took hours instead of minutes.

    The reduction is exact rather than an approximation, and rests on one fact
    asserted below: the contact window closes at 19:00 IST and the submission
    cutoff is 23:50, so **inside the window the cutoff can never bind**. What
    is left is "is there a contact-window hour between 24 and 96 hours before
    this slot", which is a prefix sum.
    """
    if CONTACT_CLOSE_IST > PDN_CUTOFF_HOUR_IST:  # pragma: no cover - guards the reduction
        raise RuntimeError(
            "the contact window now extends past the submission cutoff, so the "
            "cutoff can bind inside it and this mask is no longer exact"
        )

    # Hours at which a notice may lawfully be *sent*, ignoring which debit it
    # precedes — that part is the offset window below.
    sendable = np.fromiter(
        (is_contact_window(decided_at + timedelta(hours=h)) for h in range(horizon_slots)),
        dtype=bool,
        count=horizon_slots,
    )
    prefix = np.concatenate(([0], np.cumsum(sendable, dtype=np.int64)))

    feasible = np.zeros(horizon_slots, dtype=bool)
    for slot in range(MIN_NOTICE_HOURS, horizon_slots):
        lo = max(0, slot - MAX_NOTICE_HOURS)
        hi = slot - MIN_NOTICE_HOURS
        if hi >= lo:
            feasible[slot] = bool(prefix[hi + 1] - prefix[lo])
    return feasible


def _choose_slot(
    *,
    priors: PriorTable,
    rail: str,
    mcc: str,
    amount_paise: int,
    due_at: datetime,
    deadline_at: datetime,
    decided_at: datetime,
    attempts_remaining: int,
    p_recoverable: float,
    attempts_used: int = 0,
    days_since_success: float = 0.0,
    successful_cycles: int = 0,
    revocation: RevocationModel | None = None,
) -> int | None:
    """The slot the DP picks, or None when §23.3 says do not attempt."""
    mask = build_mask(
        adapter_for(rail),
        decided_at=decided_at,
        deadline_at=deadline_at,
        horizon_slots=DEFAULT_HORIZON_SLOTS,
    )
    # A debit nobody may lawfully notice is not a lawful debit (§30.1).
    legal = mask.combined & _notice_feasible_mask(
        decided_at=decided_at, horizon_slots=DEFAULT_HORIZON_SLOTS
    )
    if not legal.any():
        return None

    # ADR-075's presence formulation. The prior is already a per-slot rate, so
    # it is read as P(funds present at t) directly. Illegal slots are zeroed,
    # matching `serving._presence_curve`: a non-zero value in a slot nothing
    # can act on is a number waiting to be misread.
    presence = presence_curve(
        priors,
        mcc=mcc,
        rail=rail,
        amount_paise=amount_paise,
        due_day_of_month=due_at.day,
        horizon_slots=DEFAULT_HORIZON_SLOTS,
    )
    presence = np.where(legal, presence, 0.0)

    policy = solve(
        presence=presence,
        legal=legal,
        cost=attempt_cost_matrix(
            amount_paise=amount_paise,
            budget=attempts_remaining,
            horizon_slots=DEFAULT_HORIZON_SLOTS,
        ),
        amount_paise=amount_paise,
        continuation_value_paise=continuation_value_paise(amount_paise),
        dr=revocation_delta(
            DEFAULT_HORIZON_SLOTS,
            model=revocation or _bootstrap_revocation_model(),
            features=_revocation_features(
                rail=rail,
                attempts_used=attempts_used,
                days_since_success=days_since_success,
                successful_cycles=successful_cycles,
            ),
        ),
        health=health_multiplier(DEFAULT_HORIZON_SLOTS),
        budget=attempts_remaining,
        lead_slots=PDN_LEAD_HOURS,
        p_recoverable=p_recoverable,
    )
    return policy.best_slot(attempts_remaining, 0)


async def plan_tenant(
    engine: AsyncEngine,
    tenant_id: str,
    priors: PriorTable,
    *,
    now: datetime | None = None,
    offset: int = 0,
) -> TickResult:
    """Plan one tenant's due cycles, starting `offset` rows into the queue."""
    decided_at = now or datetime.now(UTC)

    async with tenant_transaction(engine, tenant_id) as conn:
        stage = await current_stage(conn, tenant_id)
        if not may_decide(stage):
            return TickResult(0, 0, 0, 0, 0, examined=0)

        rows = list(
            await conn.execute(
                _CANDIDATES,
                {
                    "tenant_id": tenant_id,
                    "now": decided_at,
                    "pending": STATE_PENDING,
                    "claimed": STATE_CLAIMED,
                    "batch": PAGE,
                    "offset": offset,
                },
            )
        )

        considered = scheduled = stopped = shadowed = proposed = 0
        for row in rows:
            if considered >= BATCH:
                break
            # The cohort gates *scheduling*, not deciding. SHADOW has a
            # mandate_share of 0.0 because it acts on nothing, yet §44 has it
            # "decide and log" across the portfolio — that is the comparison
            # the stage exists to produce. Applying the share here would make
            # SHADOW decide nothing at all.
            if may_fire(stage):
                arm = arm_for(stage, tenant_id=tenant_id, mandate_id=row.mandate_id)
                if arm is not Arm.TREATMENT:
                    # Outside the stage's share, or deliberately held out. A
                    # holdout that gets scheduled stops being a control arm.
                    metrics.increment("planner_skipped", tenant_id=tenant_id, arm=str(arm))
                    continue

            considered += 1

            # §24.3 ranks the permanent fix above a retry: moving the debit day
            # eliminates the failure rather than recovering from it monthly.
            # Proposed alongside the retry rather than instead of it — an
            # amendment needs the customer's agreement, and this cycle still
            # needs collecting in the meantime (ADR-102).
            if await maybe_propose_date_change(
                conn,
                tenant_id=tenant_id,
                mandate_id=str(row.mandate_id),
                cycle_id=str(row.cycle_id),
                debit_day=row.due_at.day,
                mcc=str(row.mcc or "0000"),
                rail=str(row.rail),
                ticket_band=ticket_band(int(row.amount_paise)),
                priors=priors,
                decided_at=decided_at,
            ):
                proposed += 1

            attempts_remaining = int(row.attempt_budget) - int(row.attempts_used)
            code = await _latest_decline_code(conn, tenant_id, row.mandate_id)

            slot = _choose_slot(
                priors=priors,
                rail=str(row.rail),
                mcc=str(row.mcc or "0000"),
                amount_paise=int(row.amount_paise),
                due_at=row.due_at,
                deadline_at=row.deadline_at,
                decided_at=decided_at,
                attempts_remaining=attempts_remaining,
                p_recoverable=_recoverable_probability(code),
                attempts_used=int(row.attempts_used),
                days_since_success=max((decided_at - row.due_at).total_seconds() / 86400.0, 0.0),
            )

            if slot is None:
                # §23.3: stopping is an output, not a failure to plan.
                stopped += 1
                metrics.increment("planner_stopped_on_ev", tenant_id=tenant_id)
                log.info(
                    "planner.stop",
                    extra={"cycle_id": row.cycle_id, "attempts_remaining": attempts_remaining},
                )
                continue

            fire_at = decided_at + timedelta(hours=slot)

            # §30.1 requires a pre-debit notice at least 24h ahead, and
            # `RBI-EMANDATE-PDN-24H` reads `cycles.pdn_sent_at` at fire time.
            # Scheduling the debit without also scheduling its notice produces
            # an action the gate is guaranteed to deny (FINDING-P17-11).
            notice = notification_plan(
                debit_at=fire_at,
                decided_at=decided_at,
                predicted_funding_at=None,
                risk=0.0,
            )

            if not may_fire(stage):
                # SHADOW decides and logs, fires nothing (§44). Queueing an
                # action the executor would only cancel would make the shadow
                # arm indistinguishable from a broken treatment arm.
                shadowed += 1
                metrics.increment("planner_shadow_decision", tenant_id=tenant_id)
                log.info(
                    "planner.shadow_decision",
                    extra={"cycle_id": row.cycle_id, "slot": slot, "fire_at": fire_at.isoformat()},
                )
                continue

            if not notice.will_send:
                # §24.6: choosing silence is an outcome. Without a notice the
                # debit would be denied, so neither is queued and the reason is
                # recorded rather than the pair being scheduled to fail.
                stopped += 1
                metrics.increment("planner_no_lawful_notice", tenant_id=tenant_id)
                log.info(
                    "planner.no_lawful_notice",
                    extra={
                        "cycle_id": row.cycle_id,
                        "reason": notice.suppressed_reason,
                        "debit_at": fire_at.isoformat(),
                    },
                )
                continue

            # Deterministic in the cycle and the attempt number, so a replayed
            # tick collides instead of queueing a second debit.
            action_id = f"{tenant_id}:{row.cycle_id}:{row.attempts_used}"
            inserted = await conn.execute(
                text(
                    "INSERT INTO scheduled_actions"
                    " (action_id, tenant_id, cycle_id, mandate_id, action_type,"
                    "  fire_at, state, payload)"
                    " VALUES (:a, :t, :c, :m, 'debit_attempt', :f, :state,"
                    "         CAST(:payload AS jsonb))"
                    " ON CONFLICT (action_id) DO NOTHING"
                ),
                {
                    "a": action_id,
                    "t": tenant_id,
                    "c": row.cycle_id,
                    "m": row.mandate_id,
                    "f": fire_at,
                    "state": STATE_PENDING,
                    "payload": f'{{"amount_paise": {int(row.amount_paise)}, "slot": {slot}}}',
                },
            )
            if inserted.rowcount == 0:
                # `ON CONFLICT DO NOTHING` fired: this cycle already had its
                # action. Counting the attempt rather than the insert made a
                # seeding run report 42,400 actions scheduled against a queue
                # of 3,900 — the guard working, and the tally not noticing.
                continue
            assert notice.send_at is not None  # `will_send` was checked above
            await conn.execute(
                text(
                    "INSERT INTO scheduled_actions"
                    " (action_id, tenant_id, cycle_id, mandate_id, action_type,"
                    "  fire_at, state, payload)"
                    " VALUES (:a, :t, :c, :m, :type, :f, :state, CAST(:payload AS jsonb))"
                    " ON CONFLICT (action_id) DO NOTHING"
                ),
                {
                    "a": f"{action_id}:notice",
                    "t": tenant_id,
                    "c": row.cycle_id,
                    "m": row.mandate_id,
                    "type": ACTION_TYPE_NOTICE,
                    "f": notice.send_at,
                    "state": STATE_PENDING,
                    "payload": '{"channel": "console"}',
                },
            )

            scheduled += 1
            metrics.increment("planner_scheduled", tenant_id=tenant_id)
            log.info(
                "planner.scheduled",
                extra={
                    "cycle_id": row.cycle_id,
                    "slot": slot,
                    "fire_at": fire_at.isoformat(),
                    "notice_at": notice.send_at.isoformat(),
                    "hours_of_notice": notice.hours_of_notice,
                    "attempts_remaining": attempts_remaining,
                },
            )

    return TickResult(considered, scheduled, stopped, shadowed, proposed, examined=len(rows))


#: Where each tenant's next pass starts. Held in memory rather than persisted:
#: losing it on restart costs a re-scan, never correctness, and the alternative
#: is a table whose only job is to remember a row number.
_CURSOR: dict[str, int] = {}


async def tick(engine: AsyncEngine, *, now: datetime | None = None) -> TickResult:
    """One pass over every tenant, on one snapshot of the priors."""
    async with system_transaction(engine) as conn:
        priors = await load_priors(conn)

    totals = [0, 0, 0, 0, 0, 0]
    for tenant_id in await active_tenants(engine):
        try:
            offset = _CURSOR.get(tenant_id, 0)
            r = await plan_tenant(engine, tenant_id, priors, now=now, offset=offset)
            # Reset on progress, advance on none. Acting on a cycle removes it
            # from the candidate set, so every offset past that point shifts and
            # a monotonic cursor would skip rows it never read. Advancing only
            # when a page yielded nothing is what steps over a run of
            # un-actionable cycles without stepping over live ones.
            if r.considered or r.examined < PAGE:
                _CURSOR[tenant_id] = 0
            else:
                _CURSOR[tenant_id] = offset + r.examined
            totals[0] += r.considered
            totals[1] += r.scheduled
            totals[2] += r.stopped
            totals[3] += r.shadowed
            totals[4] += r.proposed
            # Aggregated because a caller driving ticks in a loop needs to know
            # whether the queue is empty or merely un-actionable this pass —
            # omitting it made every tick report zero rows read and stopped a
            # seeding run four rounds in, with thousands of cycles left.
            totals[5] += r.examined
        except Exception:
            metrics.increment("planner_tenant_error", tenant_id=tenant_id)
            log.exception("planner.tenant_failed", extra={"tenant_id": tenant_id})
    return TickResult(
        considered=totals[0],
        scheduled=totals[1],
        stopped=totals[2],
        shadowed=totals[3],
        proposed=totals[4],
        examined=totals[5],
    )


async def run(
    engine: AsyncEngine,
    *,
    poll_interval: float = POLL_INTERVAL_SECONDS,
    stop: asyncio.Event | None = None,
) -> None:
    stop = stop or asyncio.Event()
    while not stop.is_set():
        try:
            result = await tick(engine)
            if result.scheduled or result.stopped or result.shadowed or result.proposed:
                log.info(
                    "planner.tick",
                    extra={
                        "considered": result.considered,
                        "scheduled": result.scheduled,
                        "stopped": result.stopped,
                        "shadowed": result.shadowed,
                        "date_changes": result.proposed,
                    },
                )
        except Exception:
            metrics.increment("planner_tick_error")
            log.exception("planner.tick_failed")

        with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=poll_interval)


async def main() -> None:  # pragma: no cover - process entry point
    settings = Settings.from_env()
    configure(settings.log_level)
    engine = create_app_engine(settings.database_url_app)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    log.info("planner.starting", extra={"env": settings.env})
    try:
        await run(engine, stop=stop)
    finally:
        await engine.dispose()
        log.info("planner.stopped")


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(main())
