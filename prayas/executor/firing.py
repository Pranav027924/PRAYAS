"""Fire-time revalidation (Master Spec §17.4, §31).

§31: "Fire-time revalidation is specified in §17.4 and is **the most important
transaction in the system**." Invariant 3: every scheduled action revalidates
state at FIRE time, not schedule time.

§17.4's transaction, transcribed:

    BEGIN
      SELECT cycle FOR UPDATE
      revalidate: mandate active? cycle unpaid? no late capture?
                  no out-of-band payment? amount unchanged?
      UPDATE cycles SET attempts_used = attempts_used + 1
        WHERE attempts_used < attempt_budget      <- atomic; rowcount checked
      re-evaluate gate with as_of = NOW()         <- rules may have changed
      INSERT attempt (idem_key) ON CONFLICT DO NOTHING
      INSERT outbox row
    COMMIT                                        <- intent durable before any call

**Why a savepoint wraps the decrement.** §17.4 decrements before re-evaluating
the gate, and a denial must not consume budget. But §32 also requires that
"DENIED actions are logged as prominently as allowed ones - a denial is the
proof the gate works", so aborting the whole transaction would discard the very
record the denial needs to leave behind. A savepoint releases the budget while
keeping the transaction alive to write the ledger entry. The spec's ordering is
preserved exactly; only the undo is added.

Nothing here calls a provider. The transaction commits *intent* to the outbox,
and the relay acts on it afterwards - §31: calling first and recording after
"would risk a debit with no local record, the worst possible ordering."
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.domain.rails import hour_ist
from prayas.executor.claiming import (
    STATE_CANCELLED,
    STATE_DONE,
    ClaimedAction,
    release_action,
)
from prayas.executor.idempotency import idem_key
from prayas.executor.killswitch import is_stopped
from prayas.gate.engine import evaluate
from prayas.ledger.chain import append
from prayas.measure.experiment import Experiment, active_experiment
from prayas.observability import metrics

log = logging.getLogger(__name__)

#: Cycle states in which no further attempt may fire.
SETTLED_CYCLE_STATES: Final[frozenset[str]] = frozenset(
    {
        "succeeded",
        "superseded",
        "budget_exhausted",
        "stopped_economic",
        "stopped_hard",
    }
)

ALLOW: Final = "ALLOW"


@dataclass(frozen=True, slots=True)
class FireOutcome:
    """What the fire transaction decided, and why."""

    fired: bool
    reason: str
    decision_id: str
    key: str | None = None
    verdict: str = ALLOW
    detail: str = ""


async def fire_action(
    conn: AsyncConnection,
    action: ClaimedAction,
    *,
    now: datetime | None = None,
) -> FireOutcome:
    """Run §17.4 for one claimed action. Must be inside a transaction.

    Returns without raising in every ordinary refusal path - a denial, a stale
    cycle and an exhausted budget are expected outcomes that must be recorded,
    not exceptions that unwind the record.
    """
    fired_at = now or datetime.now(tz=UTC)
    decision_id = f"dec_{uuid.uuid4().hex[:24]}"

    # §34 requires propensity be logged *at decision time*, not reconstructed
    # later — a propensity inferred after the fact is not evidence about how
    # the action came to be chosen. Resolved once here and carried into every
    # ledger record this call writes, including the refusals.
    experiment = await active_experiment(conn)

    if action.cycle_id is None:
        return FireOutcome(False, "no_cycle", decision_id, detail="action has no cycle")

    # ── SELECT cycle FOR UPDATE ──────────────────────────────────────────
    cycle = (
        await conn.execute(
            text(
                "SELECT c.cycle_id, c.mandate_id, c.amount_paise, c.state,"
                "       c.attempts_used, c.attempt_budget, c.deadline_at,"
                "       c.pdn_sent_at, m.customer_id, m.state AS mandate_state, m.rail,"
                "       m.consent_ref, m.mcc"
                "  FROM cycles c JOIN mandates m ON m.mandate_id = c.mandate_id"
                " WHERE c.tenant_id = :tenant_id AND c.cycle_id = :cycle_id"
                " FOR UPDATE OF c"
            ),
            {"tenant_id": action.tenant_id, "cycle_id": action.cycle_id},
        )
    ).one_or_none()

    if cycle is None:
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        return FireOutcome(False, "cycle_missing", decision_id)

    # ── kill switch (§45, ADR-084) ───────────────────────────────────────
    # Checked *after* the cycle is loaded so the mandate is known, and *before*
    # anything is revalidated or fired. Fails closed: an unreadable switch stops
    # firing, because an operator reaches for this during an incident and that
    # is exactly when the database is least healthy.
    stopped = await is_stopped(conn, tenant_id=action.tenant_id, mandate_id=cycle.mandate_id)
    if stopped:
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        return FireOutcome(False, "killed", decision_id, detail=stopped.reason)

    # ── revalidate ───────────────────────────────────────────────────────
    stale = _revalidate(cycle, action, fired_at)
    if stale is not None:
        metrics.increment("fire_revalidation_refused", reason=stale)
        await _record(
            conn,
            decision_id=decision_id,
            action=action,
            cycle=cycle,
            verdict="STALE",
            checks=[],
            rationale=f"fire-time revalidation refused: {stale}",
            ts=fired_at,
            degraded=False,
            experiment=experiment,
            customer_id=cycle.customer_id,
        )
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        return FireOutcome(False, stale, decision_id, verdict="STALE")

    key = idem_key(cycle.cycle_id, cycle.attempts_used + 1, action.action_type, cycle.amount_paise)

    # ── atomic budget decrement, then the gate (§17.4's order) ───────────
    # The savepoint exists so a denial can release the budget without
    # discarding the ledger record the denial must leave behind.
    savepoint = await conn.begin_nested()

    decrement = await conn.execute(
        text(
            "UPDATE cycles SET attempts_used = attempts_used + 1"
            " WHERE tenant_id = :tenant_id AND cycle_id = :cycle_id"
            "   AND attempts_used < attempt_budget"
        ),
        {"tenant_id": action.tenant_id, "cycle_id": action.cycle_id},
    )
    if decrement.rowcount != 1:
        # Never read-then-write: the rowcount IS the budget check.
        await savepoint.rollback()
        metrics.increment("fire_budget_exhausted")
        await _record(
            conn,
            decision_id=decision_id,
            action=action,
            cycle=cycle,
            verdict="DENY",
            checks=[],
            rationale=(
                f"budget exhausted: {cycle.attempts_used} of "
                f"{cycle.attempt_budget} attempts already used"
            ),
            ts=fired_at,
            degraded=False,
            experiment=experiment,
            customer_id=cycle.customer_id,
        )
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_DONE)
        return FireOutcome(False, "budget_exhausted", decision_id, verdict="DENY")

    gate = await evaluate(
        conn,
        action_type=action.action_type,
        rail=cycle.rail,
        ctx=_gate_context(cycle, action, fired_at),
        as_of=fired_at.date(),
    )

    if not gate.allowed:
        await savepoint.rollback()  # budget released; the record survives
        metrics.increment("fire_gate_denied", verdict=gate.verdict)
        await _record(
            conn,
            decision_id=decision_id,
            action=action,
            cycle=cycle,
            verdict=gate.verdict,
            checks=gate.checks,
            rationale=f"gate returned {gate.verdict} at fire time",
            ts=fired_at,
            degraded=gate.degraded,
            experiment=experiment,
            customer_id=cycle.customer_id,
        )
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        return FireOutcome(False, "gate_denied", decision_id, verdict=gate.verdict)

    await savepoint.commit()

    await _record(
        conn,
        decision_id=decision_id,
        action=action,
        cycle=cycle,
        verdict=ALLOW,
        checks=gate.checks,
        rationale=f"fired attempt {cycle.attempts_used + 1} of {cycle.attempt_budget}",
        ts=fired_at,
        degraded=gate.degraded,
        chosen_action={"action_type": action.action_type, "idem_key": key},
        experiment=experiment,
        customer_id=cycle.customer_id,
    )

    # ── INSERT attempt, then outbox. Intent durable before any call. ─────
    await conn.execute(
        text(
            "INSERT INTO attempts (attempt_id, tenant_id, cycle_id, attempt_seq,"
            " idem_key, scheduled_for, fired_at, state, decision_id)"
            " VALUES (:attempt_id, :tenant_id, :cycle_id, :attempt_seq,"
            " :idem_key, :scheduled_for, :fired_at, 'fired', :decision_id)"
            " ON CONFLICT (idem_key) DO NOTHING"
        ),
        {
            "attempt_id": f"att_{uuid.uuid4().hex[:24]}",
            "tenant_id": action.tenant_id,
            "cycle_id": action.cycle_id,
            "attempt_seq": cycle.attempts_used + 1,
            "idem_key": key,
            "scheduled_for": action.fire_at,
            "fired_at": fired_at,
            "decision_id": decision_id,
        },
    )

    await conn.execute(
        text(
            "INSERT INTO outbox (tenant_id, idem_key, target, request, state)"
            " VALUES (:tenant_id, :idem_key, :target, CAST(:request AS jsonb), 'pending')"
            " ON CONFLICT (idem_key) DO NOTHING"
        ),
        {
            "tenant_id": action.tenant_id,
            "idem_key": key,
            "target": "rail:" + str(cycle.rail),
            "request": json.dumps(
                {
                    "mandate_id": cycle.mandate_id,
                    "cycle_id": cycle.cycle_id,
                    "amount_paise": cycle.amount_paise,
                    "action_type": action.action_type,
                    "decision_id": decision_id,
                }
            ),
        },
    )

    await release_action(conn, action.action_id, action.tenant_id, state=STATE_DONE)
    metrics.increment("fire_committed")
    return FireOutcome(True, "fired", decision_id, key=key)


def _revalidate(cycle: Any, action: ClaimedAction, now: datetime) -> str | None:
    """§17.4's revalidation. Returns a reason to refuse, or None to proceed.

    Every check answers a question the schedule-time decision could not: state
    may have moved since, which is the whole point of Invariant 3.
    """
    if cycle.mandate_state != "active":
        return "mandate_not_active"
    if cycle.state in SETTLED_CYCLE_STATES:
        # Covers the late capture: a paid cycle is `succeeded` by then.
        return "cycle_settled"
    if cycle.deadline_at is not None and now > cycle.deadline_at:
        return "past_deadline"
    if cycle.attempts_used >= cycle.attempt_budget:
        return "budget_exhausted"

    scheduled_amount = action.payload.get("amount_paise")
    if scheduled_amount is not None and scheduled_amount != cycle.amount_paise:
        # §31: amount is in the idempotency key, so a changed amount is a
        # different external effect and must not inherit the old schedule.
        return "amount_changed"
    return None


def _gate_context(cycle: Any, action: ClaimedAction, now: datetime) -> dict[str, Any]:
    """Facts the rule pack evaluates against, read at fire time."""
    hours_since_pdn = (
        (now - cycle.pdn_sent_at).total_seconds() / 3600.0
        if cycle.pdn_sent_at is not None
        else None
    )
    return {
        "hour_ist": hour_ist(now),
        "amount_paise": cycle.amount_paise,
        "mcc": cycle.mcc,
        "pdn_sent_at": cycle.pdn_sent_at,
        "hours_since_pdn": hours_since_pdn,
        "consent_ref": cycle.consent_ref,
        "consent_withdrawn": False,
        "is_next_day_debit": False,
        "attempts_used": cycle.attempts_used,
        "attempt_budget": cycle.attempt_budget,
        "action_type": action.action_type,
    }


async def _record(
    conn: AsyncConnection,
    *,
    decision_id: str,
    action: ClaimedAction,
    cycle: Any,
    verdict: str,
    checks: list[dict[str, Any]],
    rationale: str,
    ts: datetime,
    degraded: bool,
    experiment: Experiment | None = None,
    customer_id: str | None = None,
    chosen_action: dict[str, Any] | None = None,
) -> None:
    """Append the fire-time decision to the tenant's chain (§32).

    Written for refusals as well as firings: "a denial is the proof the gate
    works."
    """
    # ADR-048: propensity is the arm-assignment probability. The sequencer is
    # deterministic, so the chosen action's own propensity is 1.0 and carries
    # no information; the genuine randomisation is §33's assignment.
    arm = prop = None
    if experiment is not None and customer_id:
        arm = experiment.arm_for(customer_id)
        prop = experiment.propensity_for(customer_id)

    await append(
        conn,
        {
            "decision_id": decision_id,
            "ts": ts,
            "trigger_event_id": None,
            "mandate_id": cycle.mandate_id,
            "cycle_id": cycle.cycle_id,
            "action_type": action.action_type,
            "verdict": verdict,
            "feature_snapshot_ref": None,
            "model_versions": json.dumps({"executor": "p6"}),
            "cause_posterior": None,
            "liquidity_curve_ref": None,
            "revocation_hazard": None,
            "continuation_value": None,
            "candidate_actions": json.dumps([]),
            "chosen_action": json.dumps(chosen_action) if chosen_action else None,
            "rationale": rationale,
            "compliance_checks": json.dumps(checks, default=str),
            "holdout_arm": arm,
            "propensity": prop,
            "degraded": degraded,
            "outcome": None,
            "outcome_ts": None,
            "recovered_paise": None,
        },
        action.tenant_id,
    )
