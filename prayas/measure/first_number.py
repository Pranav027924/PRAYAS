"""Phase 8's artifact: the sentence, generated from measured values.

The Playbook gives a template sentence. It is generated here rather than typed,
so every figure in it traces to a run — a hand-written headline is exactly the
kind of number this phase exists to make impossible.

**The sentence deliberately does not claim liquidity awareness.** The
robustness sweep shows the lift is identical when the fitted hazard is replaced
by an uninformative prior, in every perturbation tested. The lift is real and it
survives; its *mechanism* is attempt economy — spending fewer attempts, later —
not placing an attempt where money is predicted. Claiming otherwise would be
the overstatement §35 warns about, applied to the model instead of the money.
"""

from __future__ import annotations

from dataclasses import dataclass

from prayas.measure.estimators import Interval, SrmResult
from prayas.measure.harness import ATTEMPT_BUDGET, DUNNING_WINDOW_DAYS
from prayas.measure.robustness import SweepRow


@dataclass(frozen=True, slots=True)
class FirstNumber:
    """Everything the headline sentence needs, all of it measured."""

    cycles_generated: int
    recovery_population: int
    control_pct: float
    seed: str
    git_commit: str

    control_rate: float
    treatment_rate: float
    lift: Interval
    lift_without_model: float

    recovered_paise_treatment: int
    attempts_per_recovery_treatment: float
    attempts_per_recovery_control: float

    gated_actions: int
    treatment_violations: int
    baseline_shadow_violations: int

    srm: SrmResult
    sweep: list[SweepRow]

    @property
    def model_contributed_anywhere(self) -> bool:
        return any(row.model_contributes for row in self.sweep)

    @property
    def all_perturbations_survived(self) -> bool:
        return all(row.survives for row in self.sweep)


def sentence(result: FirstNumber) -> str:
    """The Playbook's sentence, with only claims the run supports."""
    return (
        f"Across {result.recovery_population:,} simulated failed cycles "
        f"(from {result.cycles_generated:,} generated), Prayas recovered "
        f"₹{result.recovered_paise_treatment / 100:,.0f}, a "
        f"{result.lift.point * 100:+.1f} pp incremental lift over a pre-registered, "
        f"seed-committed {result.control_pct:.0%} holdout running the industry-standard "
        f"day-1/3/5 policy made lawful for Indian rails "
        f"(95% CI [{result.lift.low * 100:+.1f}, {result.lift.high * 100:+.1f}] pp), "
        f"using {result.attempts_per_recovery_treatment:.1f} attempts per recovery "
        f"versus the baseline's {result.attempts_per_recovery_control:.1f}, with "
        f"{result.treatment_violations} compliance-gate breaches across "
        f"{result.gated_actions:,} gated actions — every one replayable from a "
        f"hash-chained ledger."
    )


def caveat(result: FirstNumber) -> str:
    """What the sentence must be read alongside. Not optional."""
    if result.model_contributed_anywhere:
        return (
            "The liquidity model changed the chosen slot in at least one "
            "perturbation; see the sweep for where."
        )
    return (
        "MECHANISM: the lift is NOT attributable to liquidity forecasting. "
        f"Replacing the fitted hazard with an uninformative flat prior reproduces "
        f"the lift exactly in {len(result.sweep)}/{len(result.sweep)} perturbations. "
        "§23.1's objective charges for attempts but never for delay, so with "
        "absorbing funding the latest legal slot weakly dominates whatever shape "
        "the curve has. What the number demonstrates is attempt economy under a "
        "hard regulatory budget — fewer attempts, placed later, inside the legal "
        "windows — not payday prediction."
    )


def render(result: FirstNumber) -> str:
    """The full artifact: sentence, caveat, evidence, sweep."""
    lines = [
        "=" * 88,
        "PHASE 8 — FIRST DEFENSIBLE NUMBER",
        "=" * 88,
        "",
        sentence(result),
        "",
        "-" * 88,
        caveat(result),
        "-" * 88,
        "",
        "PROVENANCE",
        f"  assignment seed   {result.seed}   git commit {result.git_commit}",
        f"  holdout           {result.control_pct:.0%}, customer-level, deterministic (§33)",
        f"  recovery window   {DUNNING_WINDOW_DAYS} days (ADR-055)",
        f"  attempt budget    {ATTEMPT_BUDGET} retries remaining (§1: 1 execution already spent)",
        f"  SRM               chi-square {result.srm.statistic:.2f}, "
        f"p = {result.srm.p_value:.3f} — {'PASS' if result.srm.passed else 'FAIL'}",
        "",
        "PRIMARY",
        f"  control recovery    {result.control_rate:.4f}",
        f"  treatment recovery  {result.treatment_rate:.4f}",
        f"  incremental lift    {result.lift.point:+.4f} "
        f"[{result.lift.low:+.4f}, {result.lift.high:+.4f}]",
        f"  same lift with an uninformative prior: {result.lift_without_model:+.4f}",
        "",
        "EFFICIENCY (§6: baseline burns ~3.4; target below 2.0)",
        f"  treatment {result.attempts_per_recovery_treatment:.2f} attempts per recovery",
        f"  control   {result.attempts_per_recovery_control:.2f} attempts per recovery",
        "",
        "COMPLIANCE",
        f"  treatment gate breaches   {result.treatment_violations} "
        f"across {result.gated_actions:,} gated actions",
        f"  baseline, in shadow (§8)  {result.baseline_shadow_violations:,} violations "
        "for the literal 10:00 IST day-1/3/5 policy",
        "",
    ]
    return "\n".join(lines)
