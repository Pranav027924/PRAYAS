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
    Intervention.PARTIAL_COLLECTION: "needs an above-AFA-cap population",
}

#: §24.4 — how close to expiry a card must be before migration is proposed.
#: One billing cycle plus a margin: proposing earlier is noise, and proposing
#: later leaves no time for the customer to complete a fresh AFA.
EXPIRY_HORIZON_DAYS: Final = 45

#: §24.4 — "repeatedly fraud-held". Two is a pattern; one is an incident.
FRAUD_HOLD_THRESHOLD: Final = 2


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


@dataclass(frozen=True, slots=True)
class RailMigrationProposal:
    """§24.4's output. **A proposal, never an action.**

    §24.4: "Requires fresh AFA, so it is a customer-consented flow, not an
    automatic action." So this carries no authority to debit and no way to
    acquire one — migrating means the customer authenticating a new mandate on
    the target rail, which produces a new `consent_ref` through the normal
    enrolment path. Nothing here can manufacture that.

    The asymmetry is deliberate: acting on a wrong proposal costs an unwanted
    prompt, while auto-migrating would debit on a mandate the customer never
    authorised — Invariant 1, and the reason §24.4 says "not an automatic
    action" rather than leaving it to judgement.
    """

    from_rail: str
    to_rail: str
    reason: str
    days_to_expiry: int | None = None

    @property
    def requires_fresh_afa(self) -> bool:
        """Always. Stated as a property so a caller cannot forget to ask."""
        return True

    @property
    def rationale(self) -> str:
        window = (
            f", {self.days_to_expiry} days to expiry" if self.days_to_expiry is not None else ""
        )
        return (
            f"propose migration {self.from_rail} -> {self.to_rail}: {self.reason}{window}"
            f" (requires fresh AFA; customer-consented, not automatic)"
        )


def propose_rail_migration(
    *,
    current_rail: str,
    available_rails: list[str],
    days_to_expiry: int | None = None,
    fraud_holds: int = 0,
    expiry_horizon_days: int = EXPIRY_HORIZON_DAYS,
) -> RailMigrationProposal | None:
    """§24.4, transcribed.

    "Card expiring or repeatedly fraud-held, with UPI Autopay available:
    propose migration before the mandate lapses. Requires fresh AFA, so it is a
    customer-consented flow, not an automatic action. **High value on the card
    e-mandate rail specifically.**"

    That last sentence is a scope limit, not a remark: the trigger conditions
    are card failure modes. A UPI mandate does not expire and an eNACH mandate
    does not fraud-hold, so proposing migration off them would be inventing a
    reason.
    """
    if current_rail != "card_emandate":
        return None

    alternates = [r for r in available_rails if r != current_rail]
    if not alternates:
        # Nothing to migrate *to*. §24.4's condition is "with UPI Autopay
        # available"; without an alternate this is a lapse, not a migration.
        return None

    # Prefer UPI Autopay, as §24.4 names it explicitly.
    target = "upi_autopay" if "upi_autopay" in alternates else alternates[0]

    expiring = days_to_expiry is not None and 0 <= days_to_expiry <= expiry_horizon_days
    repeatedly_held = fraud_holds >= FRAUD_HOLD_THRESHOLD
    if not (expiring or repeatedly_held):
        return None

    reason = "card expiring" if expiring else f"{fraud_holds} fraud holds"
    return RailMigrationProposal(
        from_rail=current_rail,
        to_rail=target,
        reason=reason,
        days_to_expiry=days_to_expiry if expiring else None,
    )


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
