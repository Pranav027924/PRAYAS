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

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

import numpy as np
from numpy.typing import NDArray

from prayas.domain.rails import IST, UpiAutopayAdapter
from prayas.inference.cause import infer, is_terminal
from prayas.measure.assignment import CONTROL, TREATMENT, arm, propensity
from prayas.policy.baseline import BASELINE_RETRY_DAYS
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


def funding_slot(cycle: SimulatedCycle) -> int | None:
    """The slot at which the account genuinely held money, or None.

    Ground truth. Read here only to *score* an attempt after the fact — never
    passed to a policy (ADR-030).
    """
    if cycle.truth.true_funding_time is None:
        return None
    delta = cycle.truth.true_funding_time - cycle.due_at
    hours = int(delta.total_seconds() // 3600)
    return hours if 0 <= hours < HORIZON_SLOTS else None


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


def survival_from(hazards: FloatArray) -> FloatArray:
    """S(t) = prod (1 - h(i)) for i <= t."""
    return np.clip(np.cumprod(1.0 - hazards), 1e-9, 1.0)


def legal_mask(
    due_at: datetime,
    horizon: int = HORIZON_SLOTS,
    *,
    window_days: int = DUNNING_WINDOW_DAYS,
) -> NDArray[np.bool_]:
    """Slots at which an attempt would be lawful, for **either** arm.

    Three constraints, applied identically to control and treatment because
    §33 requires the control run "through the same compliance gate":

    * the rail's non-peak execution windows, evaluated in IST (§1);
    * the 24h notice lead (§30.1 RBI-EMANDATE-PDN-24H), so no attempt can
      precede a lawful PDN;
    * the cycle deadline (§23.4, ADR-055), after which the cycle is written off.
    """
    slots = np.arange(horizon)
    windows = np.array(
        [ADAPTER.is_execution_legal(due_at + timedelta(hours=int(t))) for t in slots],
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
) -> tuple[list[int], bool]:
    """Slots the DP chooses, re-solving after each failure.

    `t` is in the DP's state because it conditions the hazard, so after a
    failure at `t` the next choice is made from `t` rather than from zero.
    Returns the slots and whether the policy stopped early on economic grounds
    (§23.3) rather than exhausting the budget.
    """
    survival = survival_from(hazards)
    policy = solve(
        survival=survival,
        legal=legal,
        cost=attempt_cost_matrix(amount_paise=amount_paise, budget=budget, horizon_slots=horizon),
        amount_paise=amount_paise,
        continuation_value_paise=continuation_value_paise(amount_paise),
        dr=revocation_delta(horizon),
        health=health_multiplier(horizon),
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
) -> CycleResult:
    """Run one cycle under its arm, scoring against ground truth."""
    legal = legal_mask(cycle.due_at)
    funded_at = funding_slot(cycle)
    p_recoverable = _recoverable_probability(cycle)

    if assigned == CONTROL:
        slots, stopped = control_slots(cycle.due_at), False
    else:
        slots, stopped = treatment_slots(
            hazards, legal, amount_paise=cycle.amount_paise, p_recoverable=p_recoverable
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
        # Ground truth decides: money was there, or it was not.
        if funded_at is not None and slot >= funded_at:
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


def run_batch(
    population: list[SimulatedCycle],
    *,
    seed: str,
    control_pct: float,
    train_fraction: float = 0.3,
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

    hazards = empirical_hazards(train)

    results = [
        run_arm(
            cycle,
            arm(cycle.customer_id, seed, control_pct),
            hazards=hazards,
            control_pct=control_pct,
        )
        for cycle in evaluate
    ]
    return RunResult(seed=seed, control_pct=control_pct, cycles=results)


def arm_counts(result: RunResult) -> tuple[int, int]:
    """(control, treatment) sizes, for the SRM check."""
    return (
        sum(1 for c in result.cycles if c.arm == CONTROL),
        sum(1 for c in result.cycles if c.arm == TREATMENT),
    )
