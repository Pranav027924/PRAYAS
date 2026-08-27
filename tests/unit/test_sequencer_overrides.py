"""Hard overrides, evaluated before economics (Master Spec §23.4).

"An economic argument must never be able to override a legal one." That is
asserted structurally here, not just per-predicate: the last test drives a
scenario where the DP wants to attempt and the hard stop still wins.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prayas.sequencer.overrides import (
    TERMINAL_CAUSES,
    CycleContext,
    hard_stops,
    is_hard_stopped,
)

NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _ctx(**overrides: object) -> CycleContext:
    """A cycle with nothing wrong with it, minus whatever the test breaks."""
    base: dict[str, object] = {
        "cause": "no_funds",
        "mandate_state": "active",
        "customer_opted_out": False,
        "attempts_used": 1,
        "attempt_budget": 4,
        "deadline_at": NOW + timedelta(days=10),
        "now": NOW,
    }
    base.update(overrides)
    return CycleContext(**base)  # type: ignore[arg-type]


def test_a_healthy_cycle_has_no_hard_stops() -> None:
    """The baseline must be clean, or every other test passes vacuously."""
    assert hard_stops(_ctx()) == []
    assert is_hard_stopped(_ctx()) is False


@pytest.mark.parametrize("cause", sorted(TERMINAL_CAUSES))
def test_terminal_causes_stop_the_cycle(cause: str) -> None:
    """§20 — codes resolving to a dead credential or mandate end it.

    "No model needed, no model wanted." No retry schedule recovers these.
    """
    stops = hard_stops(_ctx(cause=cause))
    assert [s.reason for s in stops] == ["terminal_cause"]
    assert cause in stops[0].detail


@pytest.mark.parametrize("state", ["paused", "at_risk", "revoked", "expired", "created"])
def test_a_mandate_that_is_not_active_stops_the_cycle(state: str) -> None:
    """§23.4 — `c.mandate.state != "active"`."""
    assert [s.reason for s in hard_stops(_ctx(mandate_state=state))] == ["mandate_not_active"]


def test_customer_opt_out_stops_the_cycle() -> None:
    assert [s.reason for s in hard_stops(_ctx(customer_opted_out=True))] == ["customer_opted_out"]


def test_budget_exhaustion_stops_the_cycle() -> None:
    """The regulator's cap is not an economic input; it is a wall."""
    stops = hard_stops(_ctx(attempts_used=4, attempt_budget=4))
    assert [s.reason for s in stops] == ["budget_exhausted"]
    assert "4 of 4 used" in stops[0].detail


def test_budget_exhaustion_triggers_when_over_budget_not_only_at_it() -> None:
    """`>=`, not `==`. An over-budget cycle is worse, not exempt."""
    assert is_hard_stopped(_ctx(attempts_used=5, attempt_budget=4))


def test_one_attempt_below_budget_does_not_stop() -> None:
    """The boundary in the safe direction — 3 of 4 used is still actionable."""
    assert not is_hard_stopped(_ctx(attempts_used=3, attempt_budget=4))


def test_passing_the_deadline_stops_the_cycle() -> None:
    assert [s.reason for s in hard_stops(_ctx(now=NOW + timedelta(days=11)))] == ["past_deadline"]


def test_exactly_at_the_deadline_does_not_stop() -> None:
    """§23.4 uses `now() > deadline_at`, strictly. The mask handles the rest."""
    deadline = NOW + timedelta(days=10)
    assert not is_hard_stopped(_ctx(now=deadline, deadline_at=deadline))


def test_every_applicable_stop_is_reported_not_merely_the_first() -> None:
    """Mirrors §30.2's "evaluate ALL — never short-circuit".

    A reviewer asking "was the budget check performed?" needs a positive
    answer regardless of what else fired first.
    """
    stops = hard_stops(
        _ctx(
            cause="mandate_dead",
            mandate_state="revoked",
            customer_opted_out=True,
            attempts_used=4,
            attempt_budget=4,
            now=NOW + timedelta(days=99),
        )
    )
    assert [s.reason for s in stops] == [
        "terminal_cause",
        "mandate_not_active",
        "customer_opted_out",
        "budget_exhausted",
        "past_deadline",
    ]


def test_a_favourable_expected_value_cannot_overturn_a_hard_stop() -> None:
    """§23.4's central guarantee, asserted end to end.

    The DP is given a scenario it loves — certain funding, no cost, no
    revocation hazard, so every state has a large positive value. The hard
    stop must still apply, and the caller's ordering must consult it first.
    """
    import numpy as np

    from prayas.sequencer.dp import solve

    horizon = 12
    policy = solve(
        survival=np.clip(np.cumprod(np.full(horizon, 0.5)), 1e-6, 1.0).astype(np.float64),
        legal=np.ones(horizon, dtype=np.bool_),
        cost=np.zeros((5, horizon), dtype=np.int64),
        amount_paise=10_000_000,
        continuation_value_paise=120_000_000,
        dr=np.zeros(horizon, dtype=np.float64),
        health=np.ones(horizon, dtype=np.float64),
        budget=4,
        lead_slots=0,
        p_recoverable=1.0,
    )

    # Economics is emphatic: attempt.
    assert not policy.should_stop(4, 0)
    assert policy.expected_value_paise(4, 0) > 0

    # The mandate is revoked. Nothing about the above may matter.
    ctx = _ctx(mandate_state="revoked")
    assert is_hard_stopped(ctx), "a revoked mandate must stop regardless of EV"

    # The predicates have no access to the policy — checked structurally, so
    # the ordering cannot be undone by a future refactor passing EV in.
    assert "value" not in CycleContext.__slots__
    assert "policy" not in CycleContext.__slots__
