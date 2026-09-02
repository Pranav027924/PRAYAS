"""Proposing §24.3's permanent fix (ADR-102).

`propose_date_change` has existed since Phase 10, fully specified and fully
tested, and **nothing called it**. That is the fifth library in this codebase
found built, correct and unrun — after the projector, the decision service, the
aggregator and the notification planner.

It matters more than the count suggests. §24.3 ranks a date change *above* a
retry: moving a ₹2,499 gym debit from the 1st to the 5th eliminates the failure
rather than recovering from it every month. A retry is a monthly cost; the
amendment is paid once.

**It is a proposal, not an amendment.** Changing a mandate's debit date needs
the customer's agreement and a rail-level amendment; this records that the
system would recommend it, with the lift that justifies it, and stops there.
Anything further would be acting on a customer's mandate without asking.

**The day-of-month hazard comes from the same priors the sequencer uses.**
§24.3 compares "funding on day 5" against "debiting on day 1", which needs
liquidity per *calendar* day rather than per slot within a cycle — exactly what
`segment_priors` is keyed on, and what the bootstrap supplies before any tenant
has cleared §27's floors.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from hashlib import sha256

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.adoption.cohort import Arm, arm_for
from prayas.adoption.stages import Stage, may_fire
from prayas.inference.bands import ticket_band
from prayas.ledger.chain import append
from prayas.models.live import PriorSource, PriorTable
from prayas.observability import metrics
from prayas.retention.interventions import (
    CHRONIC_CYCLES,
    DayOfMonthHazard,
    InterventionError,
    propose_date_change,
)

log = logging.getLogger(__name__)

#: `DayOfMonthHazard.min_observations` is 30, and it guards against inventing a
#: lift from a *thin empirical cell* — a day where three customers happened to
#: pay and the rate means nothing.
#:
#: A bootstrap cell is not a thin sample; it is a population model (ADR-098),
#: and `n_obs = 1` there encodes "yield to any real evidence" for §21's
#: shrinkage rather than "one customer did this". Passing that 1 through would
#: silence §24.3 permanently on any deployment below §27's contributor floor —
#: which, with tenants on disjoint (mcc, rail) segments, is every deployment.
#:
#: So a bootstrap-sourced table is presented at the floor it is entitled to and
#: no more. Observed cells always carry their own count.
BOOTSTRAP_OBSERVATIONS = 30


def hazard_from_priors(priors: PriorTable, *, mcc: str, rail: str, ticket: int) -> DayOfMonthHazard:
    """P(funded) by day of month, read off the priors the sequencer uses.

    Both must come from the same source: a date change argued from one model
    while the retry timing is argued from another would be two systems
    disagreeing about the same customer.
    """
    bootstrap = priors.source is PriorSource.BOOTSTRAP
    p_by_day: dict[int, float] = {}
    observations: dict[int, int] = {}
    for day in range(1, 32):
        # Band 0 is the pre-10:00 window — legal on every rail, and the band a
        # salary credit actually lands in.
        key = priors.key_for(mcc, ticket, rail, day, 0)
        cell = priors.cells.get(key)
        # `PriorTable.hazard` already returns bootstrap cells unshrunk, so the
        # rate is read the same way for both sources. Only the *support* differs.
        p_by_day[day] = priors.hazard(key)
        observations[day] = BOOTSTRAP_OBSERVATIONS if bootstrap else (cell[1] if cell else 0)
    return DayOfMonthHazard(p_by_day=p_by_day, observations=observations)


async def _consecutive_failures(conn: AsyncConnection, tenant_id: str, mandate_id: str) -> int:
    """How many of this mandate's recent cycles failed, most recent first.

    §24.3's "chronic" is a *run* of high-risk cycles, not a total: a mandate
    that failed three times last year and has paid since is not chronic, and
    counting failures rather than the run would propose an amendment to a
    customer whose problem has already resolved.
    """
    rows = await conn.execute(
        text(
            "SELECT attempts_used FROM cycles"
            " WHERE tenant_id = :t AND mandate_id = :m"
            " ORDER BY due_at DESC LIMIT 12"
        ),
        {"t": tenant_id, "m": mandate_id},
    )
    run = 0
    for row in rows:
        if int(row.attempts_used or 0) == 0:
            break
        run += 1
    return run


async def maybe_propose(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    mandate_id: str,
    cycle_id: str,
    debit_day: int,
    mcc: str,
    rail: str,
    ticket_band: int,
    priors: PriorTable,
    decided_at: datetime,
) -> bool:
    """Record a date-change proposal if §24.3's two conditions both hold."""
    already = (
        await conn.execute(
            text(
                "SELECT 1 FROM interventions"
                " WHERE tenant_id = :t AND mandate_id = :m AND kind = 'date_change'"
                " LIMIT 1"
            ),
            {"t": tenant_id, "m": mandate_id},
        )
    ).first()
    if already is not None:
        # One standing proposal per mandate. Re-proposing every cycle would
        # turn a recommendation into nagging.
        return False

    run = await _consecutive_failures(conn, tenant_id, mandate_id)
    hazard = hazard_from_priors(priors, mcc=mcc, rail=rail, ticket=ticket_band)
    try:
        proposal = propose_date_change(
            debit_day=debit_day, hazard=hazard, consecutive_high_risk_cycles=run
        )
    except InterventionError:
        # No day has enough support to compare against. Silence is correct.
        return False

    if proposal is None:
        return False

    decision_id = f"dec_{sha256(f'dc:{tenant_id}:{mandate_id}'.encode()).hexdigest()[:24]}"
    await append(
        conn,
        {
            "decision_id": decision_id,
            "ts": decided_at,
            "trigger_event_id": None,
            "mandate_id": mandate_id,
            "cycle_id": cycle_id,
            "action_type": "date_change",
            "verdict": "ALLOW",
            "feature_snapshot_ref": None,
            "model_versions": json.dumps({"planner": "p17"}),
            "cause_posterior": None,
            "liquidity_curve_ref": None,
            "revocation_hazard": None,
            "continuation_value": None,
            "candidate_actions": json.dumps([]),
            "chosen_action": json.dumps(
                {
                    "action_type": "date_change",
                    "from_day": proposal.from_day,
                    "to_day": proposal.to_day,
                }
            ),
            "rationale": proposal.rationale,
            "compliance_checks": json.dumps([]),
            "holdout_arm": None,
            "propensity": None,
            "degraded": False,
            "outcome": None,
            "outcome_ts": None,
            "recovered_paise": None,
        },
        tenant_id,
    )
    await conn.execute(
        text(
            "INSERT INTO interventions (intervention_id, tenant_id, mandate_id, kind,"
            " proposed_at, payload, decision_id)"
            " VALUES (:i, :t, :m, 'date_change', :at, CAST(:p AS jsonb), :d)"
            " ON CONFLICT (intervention_id) DO NOTHING"
        ),
        {
            "i": f"int_dc_{tenant_id}_{mandate_id}",
            "t": tenant_id,
            "m": mandate_id,
            "at": decided_at,
            "p": json.dumps(
                {
                    "from_day": proposal.from_day,
                    "to_day": proposal.to_day,
                    "expected_lift": round(proposal.expected_lift, 4),
                    "consecutive_high_risk_cycles": run,
                }
            ),
            "d": decision_id,
        },
    )
    metrics.increment("date_change_proposed", tenant_id=tenant_id)
    log.info(
        "planner.date_change_proposed",
        extra={
            "mandate_id": mandate_id,
            "from_day": proposal.from_day,
            "to_day": proposal.to_day,
            "lift": round(proposal.expected_lift, 3),
        },
    )
    return True


#: Mandates examined per sweep.
SWEEP_BATCH = 500

_CHRONIC = text(
    "SELECT m.mandate_id, m.mcc, m.rail,"
    "       max(c.amount_paise) AS amount_paise,"
    "       max(extract(day FROM c.due_at))::int AS debit_day,"
    "       max(c.cycle_id) AS cycle_id"
    "  FROM mandates m JOIN cycles c ON c.mandate_id = m.mandate_id"
    " WHERE m.tenant_id = :tenant_id"
    "   AND NOT EXISTS (SELECT 1 FROM interventions i"
    "                    WHERE i.mandate_id = m.mandate_id AND i.kind = 'date_change')"
    " GROUP BY m.mandate_id, m.mcc, m.rail"
    " HAVING count(*) FILTER (WHERE c.attempts_used > 0) >= :chronic"
    " LIMIT :batch"
)


async def sweep(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    stage: Stage,
    priors: PriorTable,
    decided_at: datetime,
    batch: int = SWEEP_BATCH,
) -> int:
    """Propose date changes across a tenant's chronic mandates.

    Its own pass, deliberately. Riding the scheduling loop tied the permanent
    fix to "this cycle needs a retry queued" — so a chronic mandate whose
    current cycle already had an attempt scheduled was excluded from the
    candidate query and never assessed again. §24.3 is a judgement about the
    *mandate*: whether its debit day is wrong, month after month. That question
    does not become irrelevant because this month's retry is already booked.
    """
    if not may_fire(stage):
        # SHADOW decides but changes nothing, and OBSERVE does neither. A
        # proposal is a recommendation to a merchant about a customer's
        # mandate, so it belongs to the stages that act.
        return 0

    rows = list(
        await conn.execute(
            _CHRONIC,
            {"tenant_id": tenant_id, "chronic": CHRONIC_CYCLES, "batch": batch},
        )
    )
    proposed = 0
    for row in rows:
        if arm_for(stage, tenant_id=tenant_id, mandate_id=str(row.mandate_id)) is not Arm.TREATMENT:
            continue
        if await maybe_propose(
            conn,
            tenant_id=tenant_id,
            mandate_id=str(row.mandate_id),
            cycle_id=str(row.cycle_id),
            debit_day=int(row.debit_day),
            mcc=str(row.mcc or "0000"),
            rail=str(row.rail),
            ticket_band=ticket_band(int(row.amount_paise)),
            priors=priors,
            decided_at=decided_at,
        ):
            proposed += 1
    return proposed
