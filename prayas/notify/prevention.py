"""Prevention accounting (Playbook Phase 9; Master Spec §6, §24.2).

"**Prevention accounting**: high-risk cycles succeeding on first execution with
zero retries consumed."

The definition is deliberately strict, and the strictness is the point. A cycle
counts as *prevented* only if it succeeded on the first execution having spent
**no** retries. A cycle that failed and was later recovered is a **recovery**,
not a prevention — counting it as both would double-count the same rupee and
inflate the one metric no competitor reports, which would be a poor thing to
be caught doing.

§6 lists prevention as its own row for the same reason: it is a different
mechanism with a different denominator, and merging it into recovery hides
whether the shift-left is working at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from prayas.measure.estimators import ArmSummary, Interval, incremental


class PreventionError(ValueError):
    """The accounting does not add up."""


@dataclass(frozen=True, slots=True)
class CycleAccounting:
    """One cycle's outcome, classified into exactly one bucket."""

    cycle_id: str
    arm: str
    high_risk: bool
    notification_sent: bool
    succeeded_first_execution: bool
    retries_consumed: int
    recovered_after_retry: bool

    def __post_init__(self) -> None:
        if self.succeeded_first_execution and self.retries_consumed:
            raise PreventionError(
                f"{self.cycle_id}: succeeded on first execution but consumed "
                f"{self.retries_consumed} retries"
            )
        if self.succeeded_first_execution and self.recovered_after_retry:
            raise PreventionError(f"{self.cycle_id}: counted as both prevented and recovered")

    @property
    def prevented(self) -> bool:
        """Succeeded first time, zero retries — the Playbook's definition."""
        return self.succeeded_first_execution and self.retries_consumed == 0

    @property
    def recovered(self) -> bool:
        """Failed, then was brought back. Disjoint from `prevented`."""
        return self.recovered_after_retry


@dataclass(frozen=True, slots=True)
class PreventionReport:
    control: ArmSummary
    treatment: ArmSummary
    control_recoveries: ArmSummary
    treatment_recoveries: ArmSummary

    @property
    def prevention_lift(self) -> Interval:
        return incremental(self.treatment, self.control)

    @property
    def recovery_lift(self) -> Interval:
        return incremental(self.treatment_recoveries, self.control_recoveries)


def account(cycles: list[CycleAccounting], *, high_risk_only: bool = True) -> PreventionReport:
    """Split prevention and recovery by arm, over the high-risk population.

    `high_risk_only` reflects the Playbook's wording — prevention is measured
    on *high-risk* cycles, because a low-risk cycle that succeeds first time
    would have done so with or without a notice, and counting those would let
    the metric rise by simply enrolling easier customers.
    """
    eligible = [c for c in cycles if c.high_risk] if high_risk_only else list(cycles)
    if not eligible:
        raise PreventionError("no eligible cycles to account for")

    def summarise(arm: str, *, prevented: bool) -> ArmSummary:
        rows = [c for c in eligible if c.arm == arm]
        hits = sum(1 for c in rows if (c.prevented if prevented else c.recovered))
        return ArmSummary(n=len(rows), recovered=hits)

    arms = {c.arm for c in eligible}
    if len(arms) < 2:
        raise PreventionError(f"need both arms to compare, saw {sorted(arms)}")
    control, treatment = sorted(arms)

    return PreventionReport(
        control=summarise(control, prevented=True),
        treatment=summarise(treatment, prevented=True),
        control_recoveries=summarise(control, prevented=False),
        treatment_recoveries=summarise(treatment, prevented=False),
    )


def assert_disjoint(cycles: list[CycleAccounting]) -> None:
    """No cycle may be counted as both prevented and recovered.

    Enforced as its own check rather than trusted, because double-counting is
    the single easiest way to inflate a prevention rate and the hardest to spot
    in an aggregate.
    """
    both = [c.cycle_id for c in cycles if c.prevented and c.recovered]
    if both:
        raise PreventionError(
            f"{len(both)} cycles counted as both prevented and recovered: {both[:5]}"
        )
