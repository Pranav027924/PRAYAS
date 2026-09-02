"""The Phase 8 harness: run the full loop and report an honest result.

"Nothing new. Integrate, run, report."

Every component here was built and verified in its own phase. This module wires
them into one pass over a seeded population and produces a number.

**Both arms are compliant.** ADR-052 holds the pre-debit notification identical
across arms, and ADR-054 moves the control's firing hour into a legal window.
Without both, the gate would deny every control attempt, control recovery would
be zero, and the "lift" would equal treatment recovery outright — a tautology.
Holding compliance constant means the only thing that varies is *which legal
slot* gets chosen, which is exactly what the sequencer decides.

**Outcomes come from ground truth, not from the model.** An attempt succeeds
iff the account was genuinely funded by then and the cause was recoverable.
`sim_ground_truth` is read through the owner role only (ADR-030), so the
policies being compared never see the labels they are scored against.

**Hazards are fitted on a training split.** The population is partitioned
before anything is estimated, so the curve the sequencer uses was never fitted
on the cycles it is measured over.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

import numpy as np
from numpy.typing import NDArray
from prayas_rulepack.baseline import BASELINE_RETRY_DAYS

from prayas.domain.rails import IST, UpiAutopayAdapter, adapter_for
from prayas.inference.cause import infer, is_terminal
from prayas.inference.hazard import leaky_survival
from prayas.inference.nowcast import IssuerNowcast
from prayas.measure.assignment import CONTROL, TREATMENT, arm, propensity
from prayas.retention.revocation import RevocationFeatures, RevocationModel
from prayas.sequencer.dp import solve
from prayas.sequencer.economics import (
    attempt_cost_matrix,
    continuation_value_paise,
    health_multiplier,
    revocation_delta,
)
from prayas.sim.generate import SimulatedCycle

FloatArray = NDArray[np.float64]

#: One slot per hour over the recovery horizon (Appendix C: slot_minutes 60).
SLOT_HOURS: Final = 1
HORIZON_SLOTS: Final = 30 * 24  # §38's 30-day horizon, hourly

#: §1 — "one execution plus up to three retries per cycle". The execution is
#: the debit that already failed and put the cycle into recovery, so **three**
#: is what remains to spend. Budgeting 4 here would hand the sequencer an
#: attempt the regulator does not permit.
ATTEMPT_BUDGET: Final = 3

#: ADR-054. The control keeps days 1/3/5 but fires at 09:00 IST, inside the
#: pre-10:00 NPCI window, so both arms are lawful and only timing varies.
CONTROL_HOUR_IST: Final = 9

#: ADR-052. Both arms give the same lawful notice, so the PDN is not part of
#: the treatment and Phase 9 still has something to improve.
PDN_LEAD_HOURS: Final = 25

#: ADR-055. The merchant's dunning window, after which the cycle is written off.
#: §36 already has `cycles.deadline_at` and §23.4 already hard-stops past it —
#: the field exists and is respected. The simulator merely set it to the full
#: 30-day horizon, so it never bound, and with no cost of delay in §23.1's
#: objective the DP correctly degenerated to "wait to the end, fire once".
#: 7 days accommodates the day-1/3/5 baseline in full, so the control arm is
#: not handicapped by the window. Swept by the robustness suite (ADR-053).
DUNNING_WINDOW_DAYS: Final = 7

ADAPTER: Final = UpiAutopayAdapter()


@dataclass(frozen=True, slots=True)
class CycleResult:
    """What happened to one cycle under its assigned arm."""

    cycle_id: str
    customer_id: str
    arm: str
    propensity: float
    amount_paise: int
    attempts_fired: int
    recovered: bool
    recovered_paise: int
    gate_denials: int
    stopped_early: bool
    decision_slots: list[int] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ArmResult:
    n: int
    recovered: int
    recovered_paise: int
    attempts: int
    gate_denials: int
    stopped_early: int

    @property
    def recovery_rate(self) -> float:
        return self.recovered / self.n if self.n else 0.0

    @property
    def attempts_per_recovery(self) -> float:
        """§6's efficiency metric. Infinite recoveries-of-zero are reported as 0."""
        return self.attempts / self.recovered if self.recovered else 0.0


@dataclass(frozen=True, slots=True)
class RunResult:
    seed: str
    control_pct: float
    cycles: list[CycleResult]

    def arm(self, which: str) -> ArmResult:
        rows = [c for c in self.cycles if c.arm == which]
        return ArmResult(
            n=len(rows),
            recovered=sum(1 for c in rows if c.recovered),
            recovered_paise=sum(c.recovered_paise for c in rows),
            attempts=sum(c.attempts_fired for c in rows),
            gate_denials=sum(c.gate_denials for c in rows),
            stopped_early=sum(1 for c in rows if c.stopped_early),
        )


def initial_debit_failed(cycle: SimulatedCycle) -> bool:
    """Did the first debit fail? Read from the observable stream, not truth.

    Selection has to be observable: a recovery experiment enrols the cycles a
    merchant can see have failed. Reading `truth.true_cause` to decide who is
    in the population would be selecting on a label (ADR-030).
    """
    return any(
        event.get("body", {}).get("event") == "payment.failed" for event in cycle.observables
    )


def recovery_population(cycles: list[SimulatedCycle]) -> list[SimulatedCycle]:
    """Cycles that actually need recovering.

    A cycle whose first debit succeeded is not a recovery opportunity — it has
    nothing to recover. Including them scores both arms on cycles neither had
    to work for, diluting the comparison with free wins.
    """
    return [c for c in cycles if initial_debit_failed(c)]


def funding_slot(cycle: SimulatedCycle, horizon: int = HORIZON_SLOTS) -> int | None:
    """The slot at which the account genuinely held money, or None.

    Ground truth. Read here only to *score* an attempt after the fact — never
    passed to a policy (ADR-030).
    """
    if cycle.truth.true_funding_time is None:
        return None
    delta = cycle.truth.true_funding_time - cycle.due_at
    hours = int(delta.total_seconds() // 3600)
    return hours if 0 <= hours < horizon else None


def funds_present(cycle: SimulatedCycle, horizon: int = HORIZON_SLOTS) -> NDArray[np.bool_]:
    """Whether the account actually held money in each hourly slot (ADR-073).

    Ground truth, read only to *score* an attempt after the fact — never passed
    to a policy (ADR-030).

    Under the default absorbing dynamics this is `slot >= funding_slot`, exactly
    what `run_arm` checked before, so every closed phase's numbers are
    unchanged. Under `non_absorbing` the windows are bounded and an attempt can
    arrive after the money has gone — which is the only condition under which
    *when* you retry can matter at all.
    """
    present = np.zeros(horizon, dtype=np.bool_)
    windows = cycle.truth.funding_windows
    if not windows:
        slot = funding_slot(cycle, horizon)
        if slot is not None:
            present[slot:] = True
        return present

    for start, end in windows:
        first = int((start - cycle.due_at).total_seconds() // 3600)
        last = int(np.ceil((end - cycle.due_at).total_seconds() / 3600))
        lo, hi = max(first, 0), min(last, horizon)
        if hi > lo:
            present[lo:hi] = True
    return present


def empirical_hazards(cycles: list[SimulatedCycle], horizon: int = HORIZON_SLOTS) -> FloatArray:
    """h(t) = funded at t / still unfunded entering t — §21's definition.

    Fitted on the training split only, and on the **recovery population** only.
    The quantity the sequencer needs is P(funded at t | not funded at the due
    date) — conditioned on having failed. Fitting across cycles that were
    already funded at t=0 puts an enormous spike at slot 0 that no attempt can
    legally act on, and flattens everything the model could actually use.
    """
    at_risk = np.zeros(horizon, dtype=np.float64)
    events = np.zeros(horizon, dtype=np.float64)

    for cycle in cycles:
        slot = funding_slot(cycle)
        last = slot if slot is not None else horizon - 1
        at_risk[: last + 1] += 1.0
        if slot is not None:
            events[slot] += 1.0

    with np.errstate(divide="ignore", invalid="ignore"):
        hazards = np.where(at_risk > 0, events / at_risk, 0.0)
    return np.clip(hazards, 1e-6, 1.0 - 1e-6)


def survival_from(hazards: FloatArray, *, leak_per_day: float = 0.0) -> FloatArray:
    """S(t) = prod (1 - h(i)) for i <= t, optionally with §21's leak.

    `leak_per_day=0` is the strictly-absorbing curve every closed phase was
    measured under.
    """
    return np.clip(leaky_survival(hazards, leak_per_day=leak_per_day), 1e-9, 1.0)


def legal_mask(
    due_at: datetime,
    horizon: int = HORIZON_SLOTS,
    *,
    window_days: int = DUNNING_WINDOW_DAYS,
    rail: str = "upi_autopay",
) -> NDArray[np.bool_]:
    """Slots at which an attempt would be lawful, for **either** arm.

    Three constraints, applied identically to control and treatment because
    §33 requires the control run "through the same compliance gate":

    * the rail's non-peak execution windows, evaluated in IST (§1);
    * the 24h notice lead (§30.1 RBI-EMANDATE-PDN-24H), so no attempt can
      precede a lawful PDN;
    * the cycle deadline (§23.4, ADR-055), after which the cycle is written off.

    `rail` defaults to UPI Autopay, which is what every phase before 14 ran on
    and what every recorded number in this file was measured under. Passing
    another rail changes only the first constraint — §9's windows differ per
    rail while the notice lead and the deadline do not.
    """
    slots = np.arange(horizon)
    adapter = adapter_for(rail)
    windows = np.array(
        [adapter.is_execution_legal(due_at + timedelta(hours=int(t))) for t in slots],
        dtype=np.bool_,
    )
    notice = slots >= PDN_LEAD_HOURS
    deadline = slots < window_days * 24
    return windows & notice & deadline


def control_slots(due_at: datetime, horizon: int = HORIZON_SLOTS) -> list[int]:
    """Day-1/3/5 at 09:00 IST (ADR-054).

    Same cadence as the mainstream default; only the hour is moved into a legal
    window so the arm is comparable rather than uniformly denied.

    Computed by constructing 09:00 on the IST calendar day rather than by
    searching hour offsets — a cycle due at, say, 07:23 IST never lands exactly
    on the hour, so an offset search silently finds nothing and the arm fires
    no attempts at all.
    """
    slots: list[int] = []
    for day in BASELINE_RETRY_DAYS:
        local = (due_at + timedelta(days=day)).astimezone(IST)
        target = local.replace(hour=CONTROL_HOUR_IST, minute=0, second=0, microsecond=0)
        slot = int((target.astimezone(UTC) - due_at.astimezone(UTC)).total_seconds() // 3600)
        if 0 <= slot < horizon:
            slots.append(slot)
    return slots


def treatment_slots(
    hazards: FloatArray,
    legal: NDArray[np.bool_],
    *,
    amount_paise: int,
    p_recoverable: float,
    horizon: int = HORIZON_SLOTS,
    budget: int = ATTEMPT_BUDGET,
    dr: FloatArray | None = None,
    health: FloatArray | None = None,
    leak_per_day: float = 0.0,
    presence: FloatArray | None = None,
) -> tuple[list[int], bool]:
    """Slots the DP chooses, re-solving after each failure.

    `t` is in the DP's state because it conditions the hazard, so after a
    failure at `t` the next choice is made from `t` rather than from zero.
    Returns the slots and whether the policy stopped early on economic grounds
    (§23.3) rather than exhausting the budget.
    """
    survival = None if presence is not None else survival_from(hazards, leak_per_day=leak_per_day)
    policy = solve(
        survival=survival,
        presence=presence,
        legal=legal,
        cost=attempt_cost_matrix(amount_paise=amount_paise, budget=budget, horizon_slots=horizon),
        amount_paise=amount_paise,
        continuation_value_paise=continuation_value_paise(amount_paise),
        dr=revocation_delta(horizon) if dr is None else dr,
        health=health_multiplier(horizon) if health is None else health,
        budget=budget,
        lead_slots=PDN_LEAD_HOURS,
        p_recoverable=p_recoverable,
    )

    slots: list[int] = []
    remaining, t = budget, 0
    while remaining > 0:
        nxt = policy.best_slot(remaining, t)
        if nxt is None:
            # policy[b][t] == -1 — stopping is an output, not a rule (§23.3).
            return slots, True
        slots.append(nxt)
        remaining -= 1
        t = nxt
    return slots, False


def _recoverable_probability(cycle: SimulatedCycle) -> float:
    """P(cause is recoverable), from the V0 posterior on the observed code.

    Uses only the decline code the provider returned — never `true_cause`.
    """
    code = None
    for event in cycle.observables:
        entity = event.get("payload", {}).get("payment", {}).get("entity", {})
        code = entity.get("error_code") or entity.get("decline_code") or code
    posterior = infer(code)
    return float(sum(p for cause, p in posterior.posterior.items() if not is_terminal(cause)))


def run_arm(
    cycle: SimulatedCycle,
    assigned: str,
    *,
    hazards: FloatArray,
    control_pct: float,
    dr: FloatArray | None = None,
    health: FloatArray | None = None,
    leak_per_day: float = 0.0,
    presence: FloatArray | None = None,
) -> CycleResult:
    """Run one cycle under its arm, scoring against ground truth.

    `hazards` is this cycle's curve. Phase 8 passed the same population curve
    for every cycle; a per-customer model passes a different one each time
    (ADR-071). Nothing else about the loop changes, which is the point: the two
    arms differ only in the numbers the sequencer is given.
    """
    legal = legal_mask(cycle.due_at)
    present = funds_present(cycle)
    p_recoverable = _recoverable_probability(cycle)

    if assigned == CONTROL:
        slots, stopped = control_slots(cycle.due_at), False
    else:
        slots, stopped = treatment_slots(
            hazards,
            legal,
            amount_paise=cycle.amount_paise,
            p_recoverable=p_recoverable,
            dr=dr,
            health=health,
            leak_per_day=leak_per_day,
            presence=presence,
        )

    fired = denials = 0
    recovered = False
    fired_slots: list[int] = []

    for slot in slots[:ATTEMPT_BUDGET]:
        if not legal[slot]:
            # The gate would deny this. Counted, never fired.
            denials += 1
            continue
        fired += 1
        fired_slots.append(slot)
        # Ground truth decides: money was there *at that moment*, or it was not.
        if present[slot]:
            recovered = True
            break

    return CycleResult(
        cycle_id=cycle.cycle_id,
        customer_id=cycle.customer_id,
        arm=assigned,
        propensity=propensity(assigned, control_pct),
        amount_paise=cycle.amount_paise,
        attempts_fired=fired,
        recovered=recovered,
        recovered_paise=cycle.amount_paise if recovered else 0,
        gate_denials=denials,
        stopped_early=stopped,
        decision_slots=fired_slots,
    )


#: An economics provider: given the **full population**, return a function from
#: cycle to that cycle's `(Δr, health)` arrays. ADR-072 — the second seam,
#: alongside `HazardProvider`. Both default to the flat constants, so a caller
#: that passes neither gets Phase 8's behaviour exactly.
#:
#: **The full population, not the training split, and that is not leakage.**
#: `Δr` is not fitted here; it is *evaluated* on §22's features, and those
#: features are a customer's observable history. Handing this the failed-cycle
#: training split instead means a customer's successful cycles are invisible —
#: `successful_cycles` is then zero for every cycle in the run, `Δr` collapses
#: back to one array shared by everybody, and the per-customer signal the whole
#: exercise depends on is silently absent. `revocation_features` filters to
#: `due_at <` the cycle in hand, so nothing from the future is readable.
EconomicsProvider = Callable[
    [list[SimulatedCycle]], Callable[[SimulatedCycle], tuple[FloatArray, FloatArray]]
]


def flat_economics(
    train: list[SimulatedCycle],
) -> Callable[[SimulatedCycle], tuple[FloatArray, FloatArray]]:
    """ADR-037 and ADR-039's constants — the pre-Phase-11 behaviour."""
    dr = revocation_delta(HORIZON_SLOTS)
    health = health_multiplier(HORIZON_SLOTS)
    del train
    return lambda _cycle: (dr, health)


def revocation_features(cycle: SimulatedCycle, prior: list[SimulatedCycle]) -> RevocationFeatures:
    """§22's features for a cycle, from that customer's earlier cycles only.

    The cycle in hand has just failed, so it contributes one consecutive
    failure; everything else is history.
    """
    settled = [c for c in prior if _has_event(c, "payment.captured")]
    consecutive = 1
    for earlier in reversed(prior):
        if _has_event(earlier, "payment.captured"):
            break
        consecutive += 1

    last_success = max((c.due_at for c in settled), default=None)
    days_unpaid = (cycle.due_at - last_success).total_seconds() / 86400.0 if last_success else 60.0
    return RevocationFeatures(
        consecutive_failures=consecutive,
        days_since_success=max(days_unpaid, 0.0),
        successful_cycles=len(settled),
        rail=cycle.rail,
    )


def _has_event(cycle: SimulatedCycle, event: str) -> bool:
    return any(e.get("body", {}).get("event") == event for e in cycle.observables)


def fitted_economics(
    model: RevocationModel, nowcast: IssuerNowcast | None = None
) -> EconomicsProvider:
    """ADR-072: §22's fitted `Δr(t)` and §20's issuer health, per cycle."""

    def provider(
        train: list[SimulatedCycle],
    ) -> Callable[[SimulatedCycle], tuple[FloatArray, FloatArray]]:
        history: dict[str, list[SimulatedCycle]] = {}
        for cycle in train:
            history.setdefault(cycle.customer_id, []).append(cycle)
        for owned in history.values():
            owned.sort(key=lambda c: c.due_at)

        def economics_for(cycle: SimulatedCycle) -> tuple[FloatArray, FloatArray]:
            prior = [c for c in history.get(cycle.customer_id, []) if c.due_at < cycle.due_at]
            dr = revocation_delta(
                HORIZON_SLOTS, model=model, features=revocation_features(cycle, prior)
            )
            health = health_multiplier(
                HORIZON_SLOTS, nowcast=nowcast, issuer=cycle.issuer, at=cycle.due_at
            )
            return dr, health

        return economics_for

    return provider


#: A hazard provider: given the training split, return a function from cycle to
#: that cycle's hourly hazard curve. ADR-071 — the seam that lets a per-customer
#: model be measured through exactly the same loop as the population curve, so a
#: difference in lift is a difference in the model and not in the harness.
HazardProvider = Callable[[list[SimulatedCycle]], Callable[[SimulatedCycle], FloatArray]]


def population_hazard_provider(
    train: list[SimulatedCycle],
) -> Callable[[SimulatedCycle], FloatArray]:
    """Phase 8's behaviour: one empirical curve, shared by every cycle."""
    hazards = empirical_hazards(train)
    return lambda _cycle: hazards


def run_batch(
    population: list[SimulatedCycle],
    *,
    seed: str,
    control_pct: float,
    train_fraction: float = 0.3,
    hazard_provider: HazardProvider = population_hazard_provider,
    economics_provider: EconomicsProvider = flat_economics,
    leak_per_day: float = 0.0,
    presence_provider: HazardProvider | None = None,
) -> RunResult:
    """Run the full loop over a population, both arms.

    The first `train_fraction` of the population fits the hazard curve and is
    then excluded from measurement — a model fitted on the cycles it is scored
    over would report its own memorisation as a lift.
    """
    if not 0.0 < train_fraction < 1.0:
        raise ValueError(f"train_fraction must be in (0, 1), got {train_fraction}")

    # A recovery experiment enrols only cycles that failed. Everything else is
    # a free win for both arms and belongs outside the comparison.
    eligible = recovery_population(population)

    split = int(len(eligible) * train_fraction)
    train, evaluate = eligible[:split], eligible[split:]
    if not train or not evaluate:
        raise ValueError("recovery population too small to split into train and evaluation")

    curve_for = hazard_provider(train)
    economics_for = economics_provider(population)
    # ADR-075: when a presence provider is given, the DP consumes
    # `P(funds present at t)` and the survival curve is not used at all.
    presence_for = presence_provider(train) if presence_provider else None

    results = []
    for cycle in evaluate:
        dr, health = economics_for(cycle)
        results.append(
            run_arm(
                cycle,
                arm(cycle.customer_id, seed, control_pct),
                hazards=curve_for(cycle),
                control_pct=control_pct,
                dr=dr,
                health=health,
                leak_per_day=leak_per_day,
                presence=presence_for(cycle) if presence_for else None,
            )
        )
    return RunResult(seed=seed, control_pct=control_pct, cycles=results)


def arm_counts(result: RunResult) -> tuple[int, int]:
    """(control, treatment) sizes, for the SRM check."""
    return (
        sum(1 for c in result.cycles if c.arm == CONTROL),
        sum(1 for c in result.cycles if c.arm == TREATMENT),
    )
