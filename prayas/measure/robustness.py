"""Robustness sweep over §38's simulator parameters (ADR-053).

The pass/fail rule was fixed before any numbers existed: **the lift's 95% CI
must exclude zero under every perturbation**. A lift that only reaches
significance at the published defaults is a finding about the defaults.

Each perturbation moves one §38 parameter and re-runs the whole loop. The
`non_absorbing` case is the one §38 singles out — "money arrives AND is spent"
— because it is the stated limitation of §21's absorbing-funding assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from prayas.measure.assignment import CONTROL, TREATMENT
from prayas.measure.estimators import ArmSummary, Interval, incremental
from prayas.measure.harness import (
    RunResult,
    empirical_hazards,
    recovery_population,
    run_batch,
)
from prayas.sim.config import SimConfig
from prayas.sim.generate import SimulatedCycle, generate


@dataclass(frozen=True, slots=True)
class Perturbation:
    name: str
    config: SimConfig
    note: str


@dataclass(frozen=True, slots=True)
class SweepRow:
    name: str
    note: str
    n: int
    control_rate: float
    treatment_rate: float
    lift: Interval
    attempts_per_recovery_treatment: float
    attempts_per_recovery_control: float
    lift_without_model: float

    @property
    def survives(self) -> bool:
        """ADR-053's rule: the interval must exclude zero."""
        return self.lift.excludes_zero and self.lift.point > 0

    @property
    def model_contributes(self) -> bool:
        """Does the liquidity curve change the answer at all?

        Reported per perturbation because a lift that is identical with an
        uninformative prior cannot be attributed to liquidity awareness.
        """
        return abs(self.lift.point - self.lift_without_model) > 1e-9


def perturbations(base: SimConfig | None = None) -> list[Perturbation]:
    """One perturbation per §38 parameter the config exposes."""
    base = base or SimConfig()
    return [
        Perturbation("baseline", base, "published defaults"),
        Perturbation(
            "non_absorbing",
            replace(base, non_absorbing=True),
            "§38: money arrives AND is spent — §21's stated limitation",
        ),
        Perturbation("mask_05_none", replace(base, mask_05_rate=0.0), "no code-05 masking"),
        Perturbation(
            "mask_05_all", replace(base, mask_05_rate=1.0), "every no-funds hides behind 05"
        ),
        Perturbation("outage_heavy", replace(base, outage_lambda=0.25), "5x issuer outage rate"),
        Perturbation("outage_none", replace(base, outage_lambda=0.0), "no issuer outages"),
        Perturbation(
            "dry_heavy",
            replace(
                base,
                payday_mix={
                    "salaried_1st": 0.25,
                    "salaried_7th": 0.15,
                    "gig_irregular": 0.25,
                    "chronically_dry": 0.35,
                },
            ),
            "3.5x chronically-dry population",
        ),
        Perturbation(
            "revocation_high",
            replace(base, revocation_beta=0.12),
            "3x revocation hazard per failure",
        ),
        Perturbation(
            "base_failure_high",
            replace(base, base_failure_rate=0.35),
            "2x non-funding failure rate",
        ),
    ]


def _summarise(result: RunResult) -> tuple[ArmSummary, ArmSummary, float, float]:
    control = result.arm(CONTROL)
    treatment = result.arm(TREATMENT)
    return (
        ArmSummary(n=treatment.n, recovered=treatment.recovered),
        ArmSummary(n=control.n, recovered=control.recovered),
        treatment.attempts_per_recovery,
        control.attempts_per_recovery,
    )


def run_sweep(
    *,
    cycles: int,
    seed: int,
    assignment_seed: str,
    control_pct: float,
    base: SimConfig | None = None,
) -> list[SweepRow]:
    """Run every perturbation and report the lift under each."""
    rows: list[SweepRow] = []

    for perturbation in perturbations(base):
        population = generate(perturbation.config, seed=seed, tenant_id="t_sweep", cycles=cycles)
        result = run_batch(population, seed=assignment_seed, control_pct=control_pct)
        treat, ctrl, ta, ca = _summarise(result)

        # The same run with an uninformative prior, so each row can say whether
        # the liquidity model made any difference at all.
        eligible = recovery_population(population)
        split = int(len(eligible) * 0.3)
        flat_value = float(empirical_hazards(eligible[:split]).mean())
        without = _lift_with_flat_prior(population, assignment_seed, control_pct, flat_value)

        rows.append(
            SweepRow(
                name=perturbation.name,
                note=perturbation.note,
                n=treat.n + ctrl.n,
                control_rate=ctrl.rate,
                treatment_rate=treat.rate,
                lift=incremental(treat, ctrl),
                attempts_per_recovery_treatment=ta,
                attempts_per_recovery_control=ca,
                lift_without_model=without,
            )
        )
    return rows


def _lift_with_flat_prior(
    population: list[SimulatedCycle],
    assignment_seed: str,
    control_pct: float,
    flat_value: float,
) -> float:
    from prayas.measure.assignment import arm
    from prayas.measure.harness import HORIZON_SLOTS, RunResult, run_arm

    eligible = recovery_population(population)
    split = int(len(eligible) * 0.3)
    flat = np.full(HORIZON_SLOTS, flat_value)
    result = RunResult(
        seed=assignment_seed,
        control_pct=control_pct,
        cycles=[
            run_arm(
                c,
                arm(c.customer_id, assignment_seed, control_pct),
                hazards=flat,
                control_pct=control_pct,
            )
            for c in eligible[split:]
        ],
    )
    return result.arm(TREATMENT).recovery_rate - result.arm(CONTROL).recovery_rate


def render_sweep(rows: list[SweepRow]) -> str:
    """The robustness table, with ADR-053's verdict."""
    lines = [
        "ROBUSTNESS SWEEP (ADR-053: CI must exclude zero under every perturbation)",
        "-" * 88,
        f"{'perturbation':<20} {'n':>6} {'ctrl':>7} {'treat':>7} {'lift':>9} "
        f"{'95% CI':>20} {'model?':>7}",
    ]
    for row in rows:
        ci = f"[{row.lift.low:+.4f},{row.lift.high:+.4f}]"
        model = "yes" if row.model_contributes else "NO"
        mark = " " if row.survives else "!"
        lines.append(
            f"{mark}{row.name:<19} {row.n:>6} {row.control_rate:>7.4f} "
            f"{row.treatment_rate:>7.4f} {row.lift.point:>+9.4f} {ci:>20} {model:>7}"
        )

    survived = sum(1 for r in rows if r.survives)
    contributed = sum(1 for r in rows if r.model_contributes)
    lines += [
        "-" * 88,
        f"survived: {survived}/{len(rows)} perturbations",
        f"liquidity model changed the answer in: {contributed}/{len(rows)}",
    ]
    return "\n".join(lines)
