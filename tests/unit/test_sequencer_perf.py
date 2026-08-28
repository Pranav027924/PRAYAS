"""Solve-time budget (Playbook Phase 5).

Exit criterion: "Solve time under 15 ms at `B=4`, `H=720`." Appendix C fixes
those figures — `horizon_hours: 720`, `slot_minutes: 60`, and §1's four-attempt
budget.

§23.2 estimates "~8 ms vectorised" for its `O(B·H²)` loop. That estimate does
not survive a per-`(b, t)` numpy call at this size, which is why `solve`
vectorises whole layers; `solve_reference` is timed alongside to show the
difference is real rather than assumed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from prayas.domain.rails import UpiAutopayAdapter
from prayas.sequencer.dp import solve, solve_reference
from prayas.sequencer.economics import (
    attempt_cost_matrix,
    continuation_value_paise,
    health_multiplier,
    revocation_delta,
)
from prayas.sequencer.windows import DEFAULT_HORIZON_SLOTS, build_mask
from tests.dp_scenario import Scenario

BUDGET = 4
HORIZON = DEFAULT_HORIZON_SLOTS  # 720
SOLVE_BUDGET_MS = 15.0


def _realistic_inputs() -> Scenario:
    """A full-size instance built from the real mask and cost model."""
    decided_at = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)
    mask = build_mask(
        UpiAutopayAdapter(),
        decided_at=decided_at,
        deadline_at=decided_at + timedelta(days=28),
        horizon_slots=HORIZON,
    )

    # A payday-shaped hazard: most funding probability concentrated early.
    rng = np.random.default_rng(20260302)
    decrements = rng.uniform(0.0, 0.01, size=HORIZON)
    survival = np.clip(np.cumprod(1.0 - decrements), 1e-6, 1.0).astype(np.float64)

    amount = 49_900
    return {
        "survival": survival,
        "legal": mask.combined,
        "cost": attempt_cost_matrix(amount_paise=amount, budget=BUDGET, horizon_slots=HORIZON),
        "amount_paise": amount,
        "continuation_value_paise": continuation_value_paise(amount),
        "dr": revocation_delta(HORIZON),
        "health": health_multiplier(HORIZON),
        "budget": BUDGET,
        "lead_slots": 24,
        "p_recoverable": 0.71,
    }


def _time_ms(fn: Callable[..., object], inputs: Scenario, repeats: int = 7) -> float:
    """Best-of-N wall time. Best, not mean: scheduler noise only ever inflates."""
    best = float("inf")
    for _ in range(repeats):
        start = time.perf_counter()
        fn(**inputs)
        best = min(best, (time.perf_counter() - start) * 1000.0)
    return best


def test_solve_meets_the_15ms_budget_at_b4_h720() -> None:
    """The exit criterion, measured at Appendix C's configured size."""
    inputs = _realistic_inputs()
    solve(**inputs)  # warm numpy up first

    elapsed = _time_ms(solve, inputs)
    assert elapsed < SOLVE_BUDGET_MS, (
        f"solve took {elapsed:.2f} ms at B={BUDGET}, H={HORIZON}; budget is {SOLVE_BUDGET_MS} ms"
    )


def test_the_scenario_is_not_trivially_small() -> None:
    """Guards the benchmark: an over-restrictive mask would make it meaningless."""
    inputs = _realistic_inputs()
    legal_slots = int(inputs["legal"].sum())
    assert legal_slots > 200, (
        f"only {legal_slots} legal slots — benchmark would not exercise the DP"
    )

    policy = solve(**inputs)
    assert not policy.should_stop(BUDGET, 0), "benchmark scenario stops immediately"


@pytest.mark.slow
def test_vectorisation_is_why_the_budget_is_met() -> None:
    """Records the margin the rearrangement buys, at full size.

    Not a correctness test — equivalence is asserted in `test_sequencer_dp.py`.
    This exists so that if someone later "simplifies" `solve` back into the
    per-slot loop, the reason it was written this way is on record.
    """
    inputs = _realistic_inputs()
    fast = _time_ms(solve, inputs, repeats=3)
    reference = _time_ms(solve_reference, inputs, repeats=1)

    assert fast < reference, (
        f"vectorised {fast:.2f} ms vs reference {reference:.2f} ms — "
        "the optimisation is no longer earning its complexity"
    )
