"""The intervention selector (Master Spec §24; Playbook Phase 10).

"The sequencer chooses *when to attempt*. The intervention selector chooses
*what kind of action* — and retry is only one of six."

Two orderings are structural here, not conventional:

* **Hard overrides run before economics** (§23.4). An economic argument must
  never overturn a legal one, so `select` consults the hard stops before it
  computes any expected value.
* **Back off is a choice, not a fallthrough** (§24.6). "Do nothing, and record
  that doing nothing was chosen and why. A system that cannot select silence
  will over-message its way through its own portfolio." So back-off carries a
  rationale in rupees, exactly as a firing decision does, and reaches the
  ledger the same way.

§24.4 (rail migration) and §24.5 (partial collection) are represented in the
catalogue but not yet selectable: the first needs the card e-mandate rail
(Phase 14) and the second needs an above-AFA-cap population the simulator does
not currently produce. They are named rather than silently absent, so a reader
can see what the selector does not yet do.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from prayas.retention.revocation import RevocationFeatures, RevocationModel
from prayas.sim.generate import SimulatedCycle

#: §24.3's threshold, verbatim: `if lift > 0.25 and chronic`.
DATE_CHANGE_MIN_LIFT: Final = 0.25

#: §24.3's `history.consecutive_high_risk_cycles >= 3`.
CHRONIC_CYCLES: Final = 3

#: Appendix C `revocation_hazard.at_risk_threshold: 0.15`.
AT_RISK_THRESHOLD: Final = 0.15


class Intervention(StrEnum):
    """§24's catalogue. All six named, whether or not selectable yet."""

    RETRY = "retry"  # §24.1
    NOTIFY = "notify"  # §24.2
    DATE_CHANGE = "date_change"  # §24.3
    RAIL_MIGRATION = "rail_migration"  # §24.4 — Phase 14
    PARTIAL_COLLECTION = "partial_collection"  # §24.5 — needs above-cap population
    BACK_OFF = "back_off"  # §24.6


#: Not yet selectable, and why. Named so their absence is visible.
UNAVAILABLE: Final[dict[Intervention, str]] = {
    Intervention.RAIL_MIGRATION: "card e-mandate rail arrives in Phase 14",
    Intervention.PARTIAL_COLLECTION: "needs an above-AFA-cap population",
}


class InterventionError(ValueError):
    """The selector cannot decide on the inputs given."""


@dataclass(frozen=True, slots=True)
class DayOfMonthHazard:
    """P(funded by the due date), by day of month.

    §24.3 needs to compare "funding on day 5" against "debiting on day 1", so
    the liquidity signal has to be expressed per day of month rather than per
    slot within a cycle.
    """

    p_by_day: dict[int, float]
    observations: dict[int, int]
    min_observations: int = 30

    def p_funded(self, day: int) -> float:
        """P(funded by the due date) for a debit on this day of month."""
        if not 1 <= day <= 31:
            raise InterventionError(f"day of month must be 1-31, got {day}")
        if self.observations.get(day, 0) < self.min_observations:
            # Too thin to act on. Returning the mean would invent a lift.
            return 0.0
        return self.p_by_day[day]

    def peak_day_of_month(self) -> int:
        """The day with the highest funded probability, among populated days."""
        populated = {
            day: p
            for day, p in self.p_by_day.items()
            if self.observations.get(day, 0) >= self.min_observations
        }
        if not populated:
            raise InterventionError("no day of month has enough observations")
        return max(populated, key=lambda day: populated[day])


def day_of_month_hazard(cycles: list[SimulatedCycle]) -> DayOfMonthHazard:
    """Fit P(funded by due date) per day of month from observed cycles."""
    funded: dict[int, int] = defaultdict(int)
    total: dict[int, int] = defaultdict(int)

    for cycle in cycles:
        day = cycle.due_at.day
        total[day] += 1
        settled = not any(
            event.get("body", {}).get("event") == "payment.failed" for event in cycle.observables
        )
        funded[day] += int(settled)

    return DayOfMonthHazard(
        p_by_day={day: funded[day] / n for day, n in total.items() if n},
        observations=dict(total),
    )


@dataclass(frozen=True, slots=True)
class DateChangeProposal:
    """§24.3's output. Requires consent and a mandate amendment to apply."""

    from_day: int
    to_day: int
    expected_lift: float

    @property
    def rationale(self) -> str:
        return (
            f"debit day {self.from_day} -> {self.to_day}: funding probability "
            f"rises {self.expected_lift:.1%}, sustained across "
            f"{CHRONIC_CYCLES}+ high-risk cycles"
        )


def propose_date_change(
    *, debit_day: int, hazard: DayOfMonthHazard, consecutive_high_risk_cycles: int
) -> DateChangeProposal | None:
    """§24.3, transcribed.

        best_dom = hazard.peak_day_of_month()
        if best_dom == current: return None
        lift = hazard.p_funded(best_dom) - hazard.p_funded(current)
        chronic = history.consecutive_high_risk_cycles >= 3
        if lift > 0.25 and chronic: return DateChangeProposal(...)

    Both conditions are required. A large lift on a mandate that failed once is
    noise; a chronic mandate with a small lift is not worth the amendment and
    the consent it costs.
    """
    best = hazard.peak_day_of_month()
    if best == debit_day:
        return None

    lift = hazard.p_funded(best) - hazard.p_funded(debit_day)
    chronic = consecutive_high_risk_cycles >= CHRONIC_CYCLES
    if lift > DATE_CHANGE_MIN_LIFT and chronic:
        return DateChangeProposal(from_day=debit_day, to_day=best, expected_lift=lift)
    return None


@dataclass(frozen=True, slots=True)
class Decision:
    """What the selector chose, and the reasoning behind it."""

    intervention: Intervention
    rationale: str
    date_change: DateChangeProposal | None = None
    hard_stopped: bool = False

    @property
    def is_back_off(self) -> bool:
        return self.intervention is Intervention.BACK_OFF


def select(
    *,
    hard_stop_reasons: list[str],
    debit_day: int,
    hazard: DayOfMonthHazard,
    consecutive_high_risk_cycles: int,
    revocation: RevocationModel,
    features: RevocationFeatures,
    best_attempt_ev_paise: float,
    continuation_value_paise: int,
    messages_30d: int,
    fatigue_cap: int,
) -> Decision:
    """Choose one of §24's actions.

    Order is the specification's, and it matters:

    1. **Hard stops** (§23.4) — a terminal cause, a dead mandate, an exhausted
       budget. Economics never sees these.
    2. **Date change** (§24.3) — the permanent fix. Preferred over retrying
       because it eliminates the failure rather than recovering from it monthly.
    3. **Retry** (§24.1) — when the attempt is worth more than keeping the
       mandate untouched.
    4. **Back off** (§24.6) — chosen, and recorded, when nothing else is.
    """
    if hard_stop_reasons:
        return Decision(
            intervention=Intervention.BACK_OFF,
            rationale="hard stop: " + "; ".join(hard_stop_reasons),
            hard_stopped=True,
        )

    proposal = propose_date_change(
        debit_day=debit_day,
        hazard=hazard,
        consecutive_high_risk_cycles=consecutive_high_risk_cycles,
    )
    if proposal is not None:
        return Decision(
            intervention=Intervention.DATE_CHANGE,
            rationale=proposal.rationale,
            date_change=proposal,
        )

    # §23.3/§23.1: an attempt is worth making only if it beats keeping the
    # mandate untouched, which is what `continuation_value_paise` represents.
    if best_attempt_ev_paise > continuation_value_paise:
        surplus = best_attempt_ev_paise - continuation_value_paise
        return Decision(
            intervention=Intervention.RETRY,
            rationale=(
                f"retry: expected value Rs{best_attempt_ev_paise / 100:,.2f} exceeds the "
                f"Rs{continuation_value_paise / 100:,.2f} value of leaving the mandate "
                f"untouched by Rs{surplus / 100:,.2f}"
            ),
        )

    # §24.6 — silence, chosen deliberately and explained in rupees.
    monthly = revocation.monthly_hazard(features)
    reasons = [
        f"best attempt is worth Rs{best_attempt_ev_paise / 100:,.2f} against a "
        f"Rs{continuation_value_paise / 100:,.2f} mandate"
    ]
    if monthly >= AT_RISK_THRESHOLD:
        reasons.append(
            f"revocation hazard {monthly:.1%} is above the {AT_RISK_THRESHOLD:.0%} at-risk threshold"
        )
    if messages_30d >= fatigue_cap:
        reasons.append(f"fatigue {messages_30d} messages against a cap of {fatigue_cap}")

    return Decision(
        intervention=Intervention.BACK_OFF,
        rationale="backed off: " + "; ".join(reasons),
    )
