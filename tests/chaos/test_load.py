"""Load: 500 decisions/sec burst without a lag alarm (§42, §43; Phase 15).

**Phase 15 exit criterion:** "Load test sustains burst without lag alarm."

Driven directly against the decision path rather than through HTTP (ADR-085):
the SLO in §42 is *decision availability* and *timer fire accuracy*, not
request throughput, and an HTTP driver would measure a surface the system does
not primarily present.

**This measures a floor, not a capacity.** It runs on whatever machine CI
happens to give us, so a pass says "the decision path is not pathologically
slow", not "the system does 500/sec in production". Reporting it as the latter
would be the kind of number that survives into a pitch deck and is wrong there.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

import numpy as np
import pytest

from prayas.measure.harness import HORIZON_SLOTS, legal_mask
from prayas.sequencer.dp import solve
from prayas.sequencer.economics import attempt_cost_matrix

#: The Playbook's burst target.
TARGET_DECISIONS_PER_SEC = 500

#: §43 alerts on projector lag above this.
LAG_ALARM_SECONDS = 60.0

DUE_AT = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
AMOUNT = 149_900
BUDGET = 3


def _decide(legal: np.ndarray, cost: np.ndarray, presence: np.ndarray) -> int | None:
    policy = solve(
        presence=presence,
        legal=legal,
        cost=cost,
        amount_paise=AMOUNT,
        continuation_value_paise=1_200_000,
        dr=np.full(HORIZON_SLOTS, 0.01),
        health=np.ones(HORIZON_SLOTS),
        budget=BUDGET,
        lead_slots=25,
        p_recoverable=0.9,
    )
    slot = policy.best_slot(BUDGET, 0)
    return None if slot is None else int(slot)


@pytest.mark.slow
def test_the_decision_path_sustains_the_burst_target() -> None:
    """**Phase 15 exit criterion**, measured as a rate rather than asserted."""
    legal = legal_mask(DUE_AT)
    cost = attempt_cost_matrix(amount_paise=AMOUNT, budget=BUDGET, horizon_slots=HORIZON_SLOTS)
    rng = np.random.default_rng(0)
    # Each decision gets its own curve, so the run cannot be optimised away by
    # solving the same problem repeatedly.
    curves = [
        np.clip(rng.normal(0.3, 0.1, size=HORIZON_SLOTS), 0.0, 1.0)
        for _ in range(TARGET_DECISIONS_PER_SEC)
    ]

    started = time.perf_counter()
    decisions = [_decide(legal, cost, curve) for curve in curves]
    elapsed = time.perf_counter() - started

    rate = len(decisions) / elapsed
    assert rate >= TARGET_DECISIONS_PER_SEC, (
        f"{rate:.0f} decisions/sec, below the {TARGET_DECISIONS_PER_SEC} target"
    )
    # A run that decided nothing would be fast and worthless.
    assert any(d is not None for d in decisions)


@pytest.mark.slow
def test_the_burst_does_not_breach_the_lag_alarm() -> None:
    """A burst of one second's decisions must complete far inside §43's
    60-second lag threshold, or the queue behind it grows without bound."""
    legal = legal_mask(DUE_AT)
    cost = attempt_cost_matrix(amount_paise=AMOUNT, budget=BUDGET, horizon_slots=HORIZON_SLOTS)
    presence = np.full(HORIZON_SLOTS, 0.35)

    started = time.perf_counter()
    for _ in range(TARGET_DECISIONS_PER_SEC):
        _decide(legal, cost, presence)
    elapsed = time.perf_counter() - started

    assert elapsed < LAG_ALARM_SECONDS, (
        f"one second of burst took {elapsed:.1f}s, which would grow the queue"
    )


def test_a_single_decision_is_fast_enough_to_be_worth_batching() -> None:
    """Guards the per-decision cost directly, so a regression is located rather
    than showing up only as a rate that drifted."""
    legal = legal_mask(DUE_AT)
    cost = attempt_cost_matrix(amount_paise=AMOUNT, budget=BUDGET, horizon_slots=HORIZON_SLOTS)
    presence = np.full(HORIZON_SLOTS, 0.35)

    _decide(legal, cost, presence)  # warm any lazy import

    started = time.perf_counter()
    for _ in range(50):
        _decide(legal, cost, presence)
    per_decision_ms = (time.perf_counter() - started) / 50 * 1000

    assert per_decision_ms < 1000.0 / TARGET_DECISIONS_PER_SEC * 20, (
        f"{per_decision_ms:.2f}ms per decision is far off the burst budget"
    )
