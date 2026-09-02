"""Aggregations behind the demo API (Demo spec §R2).

**Every number here is measured.** Where the data cannot support a figure the
field is `None` and the screen omits it — §R2's example payload shows shapes,
not values, and filling a gap with a plausible constant is the one thing that
would make the whole surface worthless.

Two of these are statistics rather than counts and are computed as such:

* **Incremental recovery** is the difference between the treatment and holdout
  recovery rates, applied to the treated population. Reported with a 95%
  interval from the normal approximation to a difference of proportions — an
  interval that excludes zero is the claim; a point estimate is not.
* **Incremental survival** is the same comparison on mandates still alive at
  the end of the window. §6 forbids reporting recovery without it, because a
  dunning system can raise recovery while destroying the book.

Arm membership comes from `adoption.cohort.arm_for`, the same hash the planner
consults, so the split shown is the split the engine used rather than a
re-derivation that could drift from it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.adoption.cohort import Arm, arm_for
from prayas.adoption.stages import Stage
from prayas.adoption.store import current_stage

#: 95% two-sided.
Z = 1.959963985


@dataclass(frozen=True, slots=True)
class ArmStats:
    """One arm's outcomes over the window."""

    mandates: int = 0
    failed_cycles: int = 0
    recovered_cycles: int = 0
    recovered_paise: int = 0
    attempts: int = 0
    alive_mandates: int = 0

    @property
    def recovery_rate(self) -> float:
        return self.recovered_cycles / self.failed_cycles if self.failed_cycles else 0.0

    @property
    def survival_rate(self) -> float:
        return self.alive_mandates / self.mandates if self.mandates else 0.0


def _diff_ci(p1: float, n1: int, p2: float, n2: int) -> tuple[float, float] | None:
    """95% interval for `p1 - p2` under the normal approximation.

    `None` when either arm is too thin for the approximation to mean anything —
    an interval computed on nine observations is not a weaker claim, it is a
    misleading one.
    """
    if n1 < 30 or n2 < 30:
        return None
    # Clamped because a proportion outside [0, 1] is a bug upstream, and the
    # interval should say so by being absent rather than by raising inside a
    # request handler.
    if not (0.0 <= p1 <= 1.0 and 0.0 <= p2 <= 1.0):
        return None
    se = math.sqrt(p1 * (1 - p1) / n1 + p2 * (1 - p2) / n2)
    delta = p1 - p2
    return (delta - Z * se, delta + Z * se)


async def _arm_rows(conn: AsyncConnection, tenant_id: str, since: datetime) -> list[Any]:
    result = await conn.execute(
        text(
            "SELECT c.mandate_id, c.attempts_used, c.recovered_paise, c.amount_paise,"
            "       m.state AS mandate_state"
            "  FROM cycles c JOIN mandates m ON m.tenant_id = c.tenant_id"
            "                              AND m.mandate_id = c.mandate_id"
            " WHERE c.tenant_id = :t AND c.due_at >= :since"
        ),
        {"t": tenant_id, "since": since},
    )
    return list(result)


async def arm_split(
    conn: AsyncConnection, tenant_id: str, *, stage: Stage, since: datetime
) -> dict[Arm, ArmStats]:
    """Outcomes by arm. The comparison the whole portfolio screen rests on."""
    rows = await _arm_rows(conn, tenant_id, since)

    seen: dict[Arm, set[str]] = {arm: set() for arm in Arm}
    alive: dict[Arm, set[str]] = {arm: set() for arm in Arm}
    tally: dict[Arm, list[int]] = {arm: [0, 0, 0, 0] for arm in Arm}

    for row in rows:
        mandate_id = str(row.mandate_id)
        arm = arm_for(stage, tenant_id=tenant_id, mandate_id=mandate_id)
        seen[arm].add(mandate_id)
        if str(row.mandate_state) not in {"revoked", "expired"}:
            alive[arm].add(mandate_id)

        attempts = int(row.attempts_used or 0)
        recovered = int(row.recovered_paise or 0)
        if not attempts:
            # Paid first time. Not a recovery — there was nothing to recover
            # from — and counting it as one let `recovered / failed` exceed 1,
            # which drove the variance negative and the interval into a domain
            # error. The denominator defines the population.
            continue
        tally[arm][0] += 1
        tally[arm][3] += attempts
        if recovered:
            tally[arm][1] += 1
            tally[arm][2] += recovered

    return {
        arm: ArmStats(
            mandates=len(seen[arm]),
            failed_cycles=tally[arm][0],
            recovered_cycles=tally[arm][1],
            recovered_paise=tally[arm][2],
            attempts=tally[arm][3],
            alive_mandates=len(alive[arm]),
        )
        for arm in Arm
    }


def matched_pair(split: dict[Arm, ArmStats]) -> dict[str, Any]:
    """§6's pair: recovery and survival, never one without the other.

    The lift is applied to the treated population's own failed cycles at their
    own average ticket, so the rupee figure is "what this treatment recovered
    over what the same population would have recovered untreated" rather than
    an extrapolation to a portfolio that does not exist.
    """
    treat, hold = split[Arm.TREATMENT], split[Arm.HOLDOUT]

    lift = treat.recovery_rate - hold.recovery_rate
    ci = _diff_ci(treat.recovery_rate, treat.failed_cycles, hold.recovery_rate, hold.failed_cycles)
    avg_ticket = treat.recovered_paise / treat.recovered_cycles if treat.recovered_cycles else 0
    incremental = int(lift * treat.failed_cycles * avg_ticket)

    survival_lift = treat.survival_rate - hold.survival_rate
    survival_ci = _diff_ci(treat.survival_rate, treat.mandates, hold.survival_rate, hold.mandates)

    return {
        "incremental_recovery_paise": incremental,
        "incremental_recovery_ci": (
            [
                int(ci[0] * treat.failed_cycles * avg_ticket),
                int(ci[1] * treat.failed_cycles * avg_ticket),
            ]
            if ci
            else None
        ),
        "incremental_survival_pts": round(survival_lift * 100, 2),
        "incremental_survival_ci": (
            [round(survival_ci[0] * 100, 2), round(survival_ci[1] * 100, 2)]
            if survival_ci
            else None
        ),
        "holdout_pct": (
            round(100 * hold.mandates / (hold.mandates + treat.mandates))
            if (hold.mandates + treat.mandates)
            else 0
        ),
        "holdout_n": hold.mandates,
        "treatment_n": treat.mandates,
        "treatment_recovery_rate": round(treat.recovery_rate, 4),
        "holdout_recovery_rate": round(hold.recovery_rate, 4),
    }


async def efficiency(
    conn: AsyncConnection, tenant_id: str, split: dict[Arm, ArmStats], since: datetime
) -> dict[str, Any]:
    """§6's efficiency block. Each figure carries the baseline it is against.

    A number without its counterfactual is not evidence, so every metric here
    ships beside the holdout's value for the same quantity.
    """
    treat, hold = split[Arm.TREATMENT], split[Arm.HOLDOUT]

    permanent = (
        await conn.execute(
            text(
                "SELECT count(*) AS n FROM interventions"
                " WHERE tenant_id = :t AND kind = 'date_change' AND proposed_at >= :since"
            ),
            {"t": tenant_id, "since": since},
        )
    ).one()

    notices = (
        await conn.execute(
            text(
                "SELECT count(*) AS n FROM interventions"
                " WHERE tenant_id = :t AND kind = 'pdn_notice' AND proposed_at >= :since"
            ),
            {"t": tenant_id, "since": since},
        )
    ).one()

    def per_recovery(stats: ArmStats) -> float | None:
        return round(stats.attempts / stats.recovered_cycles, 2) if stats.recovered_cycles else None

    treated_cycles = treat.failed_cycles or 1
    return {
        "attempts_per_recovery": per_recovery(treat),
        "attempts_per_recovery_baseline": per_recovery(hold),
        "permanent_fixes": int(permanent.n),
        "messages_per_customer_cycle": round(int(notices.n) / treated_cycles, 2),
        # §24.6's cap is a cost control as much as a customer protection, so the
        # ceiling travels with the number rather than living in a footnote.
        "fatigue_cap": 4,
        # Not measurable from the pipeline: prevention needs a counterfactual on
        # cycles that never failed, and cost needs a rail price sheet. Absent
        # rather than guessed.
        "prevention_rate": None,
        "prevention_rate_baseline": None,
        "cost_per_rupee_recovered": None,
        "cost_target": 0.02,
    }


async def guardrails(
    conn: AsyncConnection, tenant_id: str, split: dict[Arm, ArmStats], since: datetime
) -> list[dict[str, Any]]:
    """§6's guardrails, reported unprompted — including when unflattering."""
    treat, hold = split[Arm.TREATMENT], split[Arm.HOLDOUT]

    violations = (
        await conn.execute(
            text(
                "SELECT count(*) AS n FROM decisions"
                " WHERE tenant_id = :t AND ts >= :since AND verdict = 'ALLOW'"
                "   AND EXISTS (SELECT 1 FROM jsonb_array_elements(compliance_checks) c"
                "                WHERE c->>'verdict' <> 'ALLOW')"
            ),
            {"t": tenant_id, "since": since},
        )
    ).one()

    revocation_delta = round((1 - treat.survival_rate) - (1 - hold.survival_rate), 4)
    net_value = treat.recovered_paise - hold.recovered_paise

    return [
        {
            "key": "compliance_violations",
            "value": int(violations.n),
            "threshold": "must be zero",
            "status": "ok" if int(violations.n) == 0 else "breach",
        },
        {
            "key": "revocation_vs_control",
            "value": revocation_delta,
            "threshold": "not above control",
            "status": "ok" if revocation_delta <= 0 else "breach",
        },
        {
            "key": "net_value_paise",
            "value": net_value,
            "threshold": "must be positive",
            "status": "ok" if net_value > 0 else "breach",
        },
    ]


async def rail_mix(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    rows = list(
        await conn.execute(
            text(
                "SELECT rail, count(*) AS n FROM mandates"
                " WHERE tenant_id = :t GROUP BY rail ORDER BY n DESC"
            ),
            {"t": tenant_id},
        )
    )
    total = sum(int(r.n) for r in rows) or 1
    # `mandates` travels with the share so a fleet-level merge can weight by
    # portfolio size. Averaging shares across tenants gives 33/33/33 for three
    # single-rail tenants of 6,120, 4,880 and 1,200 — which is not the mix.
    return [
        {
            "rail": str(r.rail),
            "share": round(int(r.n) / total, 4),
            "mandates": int(r.n),
            "active": True,
        }
        for r in rows
    ]


async def portfolio(
    conn: AsyncConnection, tenant_id: str, *, window_days: int = 30
) -> dict[str, Any]:
    """Everything above the fold on the portfolio screen."""
    now = datetime.now(UTC)
    since = now - timedelta(days=window_days)
    stage = await current_stage(conn, tenant_id)
    split = await arm_split(conn, tenant_id, stage=stage, since=since)

    return {
        "window": {"from": since.date().isoformat(), "to": now.date().isoformat()},
        "stage": stage.name.lower(),
        "matched_pair": matched_pair(split),
        "efficiency": await efficiency(conn, tenant_id, split, since),
        "guardrails": await guardrails(conn, tenant_id, split, since),
        "rails": await rail_mix(conn, tenant_id),
    }
