"""The revocation hazard model (Master Spec §22; ADR-060, ADR-062).

    r(t) = P(mandate revoked at t | alive at t)

"The second survival model, and the one that makes retention first-class."

§22 is explicit about why this is a *model* and not a guardrail metric: "A
metric tells you after the fact that you killed mandates. A model lets the
sequencer price that risk *before* acting. The difference is the difference
between a postmortem and a decision."

**Fitted on observable features only.** Every feature below is something the
system can see from its own event history — consecutive failures, days since
last success, tenure, rail, message volume. Ground truth (`MandateLifecycle.died`)
is used to *fit* and to *score*, never as an input (ADR-030).

**Why this supplies the missing cost of delay.** §22 names "days since last
successful debit" as a driver, so the hazard rises with time unpaid rather than
only with attempts. That is the term §23.1's objective lacks and whose absence
is FINDING-P8-01's root cause — see `marginal_delta`.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Final

from prayas.sim.lifecycle import MandateLifecycle

#: Buckets for consecutive failures. Capped because beyond four the population
#: thins and §27's k-anonymity floor would bite on a real portfolio.
MAX_FAILURE_BUCKET: Final = 4

#: Days-unpaid buckets, in months. Matches the billing cadence the feature is
#: measured over.
UNPAID_BUCKETS: Final[tuple[float, ...]] = (0.0, 30.0, 60.0, 90.0)

#: Below this many observations a cell falls back to the global rate rather
#: than reporting a hazard fitted on a handful of mandates (§27).
MIN_CELL_OBSERVATIONS: Final = 30


class RevocationError(ValueError):
    """The model cannot be fitted or queried as asked."""


@dataclass(frozen=True, slots=True)
class RevocationFeatures:
    """§22's features, all observable from the event history."""

    consecutive_failures: int
    days_since_success: float
    successful_cycles: int
    rail: str
    messages_30d: int = 0

    @property
    def failure_bucket(self) -> int:
        return min(max(self.consecutive_failures, 0), MAX_FAILURE_BUCKET)

    @property
    def unpaid_bucket(self) -> float:
        """Largest bucket edge at or below the observed days unpaid."""
        eligible = [edge for edge in UNPAID_BUCKETS if edge <= self.days_since_success]
        return max(eligible) if eligible else UNPAID_BUCKETS[0]

    @property
    def cell(self) -> tuple[int, float, str]:
        return (self.failure_bucket, self.unpaid_bucket, self.rail)


@dataclass(frozen=True, slots=True)
class RevocationModel:
    """Per-cell monthly revocation hazard, with a global fallback."""

    cell_hazard: dict[tuple[int, float, str], float]
    cell_counts: dict[tuple[int, float, str], int]
    global_hazard: float

    def monthly_hazard(self, features: RevocationFeatures) -> float:
        """r over one billing month, for these features.

        Falls back to the global rate on a thin cell rather than reporting a
        number fitted on too few mandates to mean anything.
        """
        cell = features.cell
        if self.cell_counts.get(cell, 0) < MIN_CELL_OBSERVATIONS:
            return self.global_hazard
        return float(self.cell_hazard[cell])

    def daily_hazard(self, features: RevocationFeatures) -> float:
        """The monthly figure converted to a per-day rate.

        `1 - (1 - m)^(1/30)`, not `m / 30`. Each day's hazard applies to the
        survivors of the previous day, so reaching a given monthly rate needs a
        slightly *higher* daily one than dividing suggests — `m / 30`
        understates, and the gap widens with the rate, exactly where the
        sequencer is most sensitive.
        """
        monthly = min(max(self.monthly_hazard(features), 0.0), 0.999999)
        return float(1.0 - (1.0 - monthly) ** (1.0 / 30.0))

    def survival(self, features: RevocationFeatures, *, days: float) -> float:
        """P(alive after `days`) holding these features constant."""
        if days < 0:
            raise RevocationError(f"days must be non-negative, got {days}")
        return float((1.0 - self.daily_hazard(features)) ** days)

    def marginal_delta(self, features: RevocationFeatures, *, days_ahead: float) -> float:
        """Δr — the extra revocation risk from waiting `days_ahead` to attempt.

        ADR-062. §23.1 writes `Δr(t)`, implying time-dependence; ADR-037 made
        it constant only because the simulator then modelled revocation as a
        constant per failure. With §22's hazard fitted, the honest value is the
        risk actually accrued by delaying — which is what finally gives the
        sequencer a reason to act early rather than at the last legal slot.
        """
        if days_ahead < 0:
            raise RevocationError(f"days_ahead must be non-negative, got {days_ahead}")
        waited = RevocationFeatures(
            consecutive_failures=features.consecutive_failures,
            days_since_success=features.days_since_success + days_ahead,
            successful_cycles=features.successful_cycles,
            rail=features.rail,
            messages_30d=features.messages_30d,
        )
        # Risk of dying before the attempt lands, under the state it will be in.
        return float(1.0 - self.survival(waited, days=days_ahead))


def _observations(
    lifecycles: list[MandateLifecycle],
) -> list[tuple[RevocationFeatures, bool]]:
    """One (features entering a month, died that month) row per billed cycle.

    Built per *step*, not per mandate: grouping by a mandate's observed
    failures mixes in survivorship, since a mandate that died early had fewer
    cycles in which to fail. That confound made an earlier read of this model
    look non-discriminating when it was not.
    """
    rows: list[tuple[RevocationFeatures, bool]] = []
    for lifecycle in lifecycles:
        consecutive, unpaid, successes = 0, 0.0, 0
        for index, cycle in enumerate(lifecycle.cycles):
            failed = any(
                event.get("body", {}).get("event") == "payment.failed"
                for event in cycle.observables
            )
            consecutive = consecutive + 1 if failed else 0
            unpaid = unpaid + 30.0 if failed else 0.0
            successes += 0 if failed else 1

            died_here = lifecycle.died and index == len(lifecycle.cycles) - 1
            rows.append(
                (
                    RevocationFeatures(
                        consecutive_failures=consecutive,
                        days_since_success=unpaid,
                        successful_cycles=successes,
                        rail=lifecycle.rail,
                    ),
                    died_here,
                )
            )
    return rows


def fit(lifecycles: list[MandateLifecycle]) -> RevocationModel:
    """Fit r(t) from observed mandate histories and their deaths."""
    rows = _observations(lifecycles)
    if not rows:
        raise RevocationError("no observations to fit on")

    at_risk: dict[tuple[int, float, str], int] = defaultdict(int)
    deaths: dict[tuple[int, float, str], int] = defaultdict(int)
    for features, died in rows:
        at_risk[features.cell] += 1
        deaths[features.cell] += int(died)

    total_deaths = sum(deaths.values())
    global_hazard = total_deaths / len(rows)

    return RevocationModel(
        cell_hazard={cell: deaths[cell] / n for cell, n in at_risk.items() if n},
        cell_counts=dict(at_risk),
        global_hazard=global_hazard,
    )


def expected_calibration_error(
    model: RevocationModel, lifecycles: list[MandateLifecycle], *, bins: int = 10
) -> float:
    """ECE of predicted monthly hazard against observed deaths.

    The same measure Phase 4 used for the liquidity model. §22's whole claim is
    that the sequencer can *price* revocation, and a miscalibrated price is a
    wrong price however good its ranking.
    """
    rows = _observations(lifecycles)
    if not rows:
        raise RevocationError("no observations to score")

    buckets: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for features, died in rows:
        predicted = model.monthly_hazard(features)
        buckets[min(int(predicted * bins), bins - 1)].append((predicted, died))

    error = 0.0
    for observations in buckets.values():
        weight = len(observations) / len(rows)
        mean_predicted = sum(p for p, _ in observations) / len(observations)
        observed = sum(1 for _, d in observations if d) / len(observations)
        error += weight * abs(mean_predicted - observed)
    return error


def at_risk_mandates(
    model: RevocationModel,
    lifecycles: list[MandateLifecycle],
    *,
    threshold: float = 0.15,
) -> list[str]:
    """Mandate ids whose current hazard exceeds Appendix C's `at_risk_threshold`.

    The nightly sweep's output: which mandates have entered §11's `at_risk`
    state and warrant an intervention before the next cycle.
    """
    if not 0.0 < threshold < 1.0:
        raise RevocationError(f"threshold must be in (0, 1), got {threshold}")

    flagged: list[str] = []
    for lifecycle in lifecycles:
        rows = _observations([lifecycle])
        if rows and model.monthly_hazard(rows[-1][0]) >= threshold:
            flagged.append(lifecycle.mandate_id)
    return flagged
