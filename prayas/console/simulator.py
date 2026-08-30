"""Screen 3: the policy simulator (Phase 16; §23.1, ADR-088).

"Drag cost, `λ`, `μ`; watch policy shift live across thousands of cycles."

The Playbook justifies this with "because the DP costs 8 ms". After Phase 15's
suffix-scan fix it costs about 0.7 ms, so a thousand cycles is comfortably
inside the two-second criterion — the screen is more responsive than the phase
was designed around, not less.

**This module cannot act, and that is structural rather than promised.** It
imports the DP and the cost model. It does not import the executor, the
scheduler, the outbox, or anything that writes. There is no function here that
takes a connection. Recomputing what a policy *would* do is a different act
from doing it, and on a system that moves money the difference should be
visible in the import list rather than asserted in a docstring.

`tests/unit/test_console_simulator.py` asserts that absence directly, the way
ADR-082 did for rule proposals: a guarantee resting on a check can be bypassed
by a caller who forgets to check.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

import numpy as np
import numpy.typing as npt

from prayas.sequencer.dp import STOP, solve
from prayas.sequencer.economics import attempt_cost_matrix

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

#: The Playbook's responsiveness target for a thousand cycles.
SIMULATION_BUDGET_SECONDS: Final = 2.0

#: Cap on how many cycles one request may simulate. §41.1's T9 is denial of
#: wallet; an uncapped `cycles` parameter is the cheapest way for a caller to
#: spend someone else's CPU.
MAX_SIMULATED_CYCLES: Final = 5_000


class SimulatorError(ValueError):
    """The requested policy is not one the sequencer could be given."""


@dataclass(frozen=True, slots=True)
class PolicyKnobs:
    """§23.1's weights, as a screen exposes them.

    `lambda_annoyance` and `mu_revocation` are named for §23.1 rather than for
    the Greek letters, because a slider labelled "μ" tells an operator nothing
    about what moving it costs a customer.
    """

    #: Multiplier on the modelled cost of an attempt.
    attempt_cost_multiplier: float = 1.0
    #: §23.1's λ — how much an attempt's annoyance counts against its value.
    lambda_annoyance: float = 1.0
    #: §23.1's μ — how heavily revocation risk is priced.
    mu_revocation: float = 1.0

    def __post_init__(self) -> None:
        for name in ("attempt_cost_multiplier", "lambda_annoyance", "mu_revocation"):
            value = getattr(self, name)
            if not np.isfinite(value) or value < 0:
                raise SimulatorError(f"{name} must be finite and non-negative, got {value}")


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """What the screen draws."""

    cycles: int
    knobs: PolicyKnobs
    #: Cycles for which the policy chooses to act at all.
    acted: int
    #: Mean chosen slot among those, or None when nothing acts.
    mean_slot: float | None
    #: Distinct slots chosen, which is how "the policy is shape-sensitive"
    #: becomes visible rather than asserted.
    distinct_slots: int
    elapsed_seconds: float

    @property
    def stop_rate(self) -> float:
        return 1.0 - (self.acted / self.cycles) if self.cycles else 0.0


def simulate(
    presence_curves: list[FloatArray],
    legal: BoolArray,
    *,
    knobs: PolicyKnobs,
    amount_paise: int,
    continuation_value_paise: int,
    revocation_delta: FloatArray,
    health: FloatArray,
    budget: int,
    lead_slots: int,
    p_recoverable: float = 0.9,
) -> SimulationResult:
    """Recompute the policy for every curve under one set of knobs.

    Pure: no I/O, no clock beyond timing itself, no writes. Given the same
    inputs it returns the same answer, which is what makes a slider's effect
    attributable to the slider.
    """
    import time

    if not presence_curves:
        raise SimulatorError("nothing to simulate")

    horizon = int(legal.shape[0])
    base_cost = attempt_cost_matrix(amount_paise=amount_paise, budget=budget, horizon_slots=horizon)
    # λ scales what an attempt costs; μ scales what revocation costs. Both act
    # on the DP's inputs rather than on its recursion, so the sequencer being
    # simulated is exactly the one that runs.
    cost = np.rint(base_cost * knobs.attempt_cost_multiplier * knobs.lambda_annoyance).astype(
        base_cost.dtype
    )
    dr = revocation_delta * knobs.mu_revocation

    started = time.perf_counter()
    chosen: list[int] = []
    for presence in presence_curves:
        policy = solve(
            presence=presence,
            legal=legal,
            cost=cost,
            amount_paise=amount_paise,
            continuation_value_paise=continuation_value_paise,
            dr=dr,
            health=health,
            budget=budget,
            lead_slots=lead_slots,
            p_recoverable=p_recoverable,
        )
        slot = policy.action[budget][0]
        if int(slot) != STOP:
            chosen.append(int(slot))
    elapsed = time.perf_counter() - started

    return SimulationResult(
        cycles=len(presence_curves),
        knobs=knobs,
        acted=len(chosen),
        mean_slot=float(np.mean(chosen)) if chosen else None,
        distinct_slots=len(set(chosen)),
        elapsed_seconds=elapsed,
    )


def simulate_default_population(
    cycles: int, knobs: PolicyKnobs, *, seed: int = 0
) -> SimulationResult:
    """Simulate against a synthetic population, for the screen's sliders.

    Deterministic in `seed`, so moving one slider is the only thing that
    changes between two renders. A screen whose baseline drifted underneath the
    control would make every comparison meaningless.

    Uses a synthetic population rather than the tenant's own cycles because
    this endpoint answers "how would the policy behave", not "what would happen
    to these customers" — and the second question invites reading live data
    into a screen whose whole guarantee is that it cannot act on it.
    """
    from datetime import UTC, datetime

    from prayas.measure.harness import HORIZON_SLOTS, legal_mask

    if not 1 <= cycles <= MAX_SIMULATED_CYCLES:
        raise SimulatorError(f"cycles must be between 1 and {MAX_SIMULATED_CYCLES}")

    rng = np.random.default_rng(seed)
    curves = [np.clip(rng.normal(0.3, 0.12, size=HORIZON_SLOTS), 0.0, 1.0) for _ in range(cycles)]

    return simulate(
        curves,
        legal_mask(datetime(2026, 6, 1, 4, 0, tzinfo=UTC)),
        knobs=knobs,
        amount_paise=149_900,
        continuation_value_paise=1_200_000,
        revocation_delta=np.full(HORIZON_SLOTS, 0.01),
        health=np.ones(HORIZON_SLOTS),
        budget=3,
        lead_slots=25,
    )
