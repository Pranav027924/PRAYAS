"""The Phase 5 artifact: candidates, values, and why the winner won."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from prayas.domain.rails import IST, UpiAutopayAdapter
from prayas.sequencer.dp import Policy, solve
from prayas.sequencer.economics import (
    attempt_cost_matrix,
    health_multiplier,
    revocation_delta,
)
from prayas.sequencer.explain import Candidate, format_explanation, rank_candidates
from prayas.sequencer.windows import build_mask

BUDGET = 4
HORIZON = 240


def _failed_cycle() -> dict[str, object]:
    """A cycle that has failed once, with a payday-shaped hazard."""
    decided_at = datetime(2026, 3, 2, 8, 0, tzinfo=UTC)
    mask = build_mask(
        UpiAutopayAdapter(),
        decided_at=decided_at,
        deadline_at=decided_at + timedelta(days=9),
        horizon_slots=HORIZON,
    )
    # Funding concentrated around slot ~72 (three days out) — a payday.
    slots = np.arange(HORIZON)
    hazard = 0.002 + 0.05 * np.exp(-(((slots - 72) / 8.0) ** 2))
    survival = np.clip(np.cumprod(1.0 - hazard), 1e-6, 1.0).astype(np.float64)

    amount = 49_900
    return {
        "survival": survival,
        "legal": mask.combined,
        "cost": attempt_cost_matrix(amount_paise=amount, budget=BUDGET, horizon_slots=HORIZON),
        "amount_paise": amount,
        # ADR-063: W is fitted per mandate state, not a flat 12A. This cycle
        # has already failed, and the fitted §22 model values such a mandate
        # near 5A — the placeholder over-valued it, which made the DP refuse
        # attempts on exactly the mandates where attempting is cheapest.
        "continuation_value_paise": amount * 5,
        # ADR-062: Δr grows with time unpaid, so waiting is no longer free.
        # Under Phase 5's constant Δr the DP took the last legal slot whatever
        # the hazard said — FINDING-P8-01 — so this fixture would not exercise
        # the payday claim it exists to make.
        "dr": revocation_delta(HORIZON) * (1.0 + np.arange(HORIZON) / 48.0),
        "health": health_multiplier(HORIZON),
        "budget": BUDGET,
        "lead_slots": 24,
        "p_recoverable": 0.71,
        "times": mask.times,
    }


def _ranked(scenario: dict[str, Any], budget_remaining: int = 3) -> tuple[Policy, list[Candidate]]:
    dp_args = {k: v for k, v in scenario.items() if k != "times"}
    policy = solve(**dp_args)
    # `rank_candidates` takes `budget_remaining`, not the cycle's full `budget`.
    rank_args = {k: v for k, v in scenario.items() if k != "budget"}
    return policy, rank_candidates(
        policy=policy,
        budget_remaining=budget_remaining,
        last_failure_slot=0,
        **rank_args,
    )


def test_every_legal_candidate_is_reported_not_only_the_winner() -> None:
    """§32 — replay needs "every candidate action with its expected value"."""
    scenario = _failed_cycle()
    _, candidates = _ranked(scenario)

    legal = scenario["legal"]
    expected = int(legal[24:].sum())  # type: ignore[index]
    assert len(candidates) == expected, (
        f"reported {len(candidates)} candidates, {expected} are legal and reachable"
    )
    assert sum(c.chosen for c in candidates) == 1


def test_candidates_are_ranked_by_expected_value() -> None:
    _, candidates = _ranked(_failed_cycle())
    values = [c.expected_value_paise for c in candidates]
    assert values == sorted(values, reverse=True)


def test_the_chosen_candidate_is_the_highest_valued_one() -> None:
    """The DP's choice and the recomputed objective must agree.

    Values here are derived from §23.1's equation, not read from the DP, so
    agreement is a real cross-check of the optimiser.
    """
    _, candidates = _ranked(_failed_cycle())
    assert candidates[0].chosen, (
        f"top candidate slot {candidates[0].slot} is not the chosen one; "
        f"chosen was slot {next(c.slot for c in candidates if c.chosen)}"
    )


def test_the_sequencer_aims_at_the_payday_not_the_calendar() -> None:
    """The thesis, made checkable.

    Funding mass sits near slot 72. A day-1/3/5 calendar policy would fire at
    the first legal slot after the notice lead. The DP should instead pick a
    slot near the hazard peak.
    """
    scenario = _failed_cycle()
    _, candidates = _ranked(scenario)
    winner = next(c for c in candidates if c.chosen)

    first_legal = min(c.slot for c in candidates)
    assert winner.slot != first_legal, "chose the earliest legal slot, i.e. a calendar"
    assert 60 <= winner.slot <= 110, (
        f"chose slot {winner.slot}; funding mass is concentrated near slot 72"
    )


def test_explanation_names_the_runner_up_and_the_margin() -> None:
    """ "The reason the chosen one won" needs something to have won against."""
    _, candidates = _ranked(_failed_cycle())
    text = format_explanation(candidates)

    assert "chosen: slot" in text
    assert "beats the next best" in text
    assert "chance of funding against" in text
    assert "₹" in text


def test_explanation_reports_stop_when_nothing_clears_the_bar() -> None:
    scenario = _failed_cycle()
    scenario["cost"] = np.full((BUDGET + 1, HORIZON), 50_000_000, dtype=np.int64)
    policy, candidates = _ranked(scenario)

    assert policy.should_stop(3, 0)
    text = format_explanation(candidates)
    assert "chosen: STOP" in text
    assert "does not clear the bar" in text


def test_no_legal_candidates_is_stated_plainly() -> None:
    scenario = _failed_cycle()
    scenario["legal"] = np.zeros(HORIZON, dtype=np.bool_)
    _, candidates = _ranked(scenario)
    assert candidates == []
    assert "no legal candidate" in format_explanation(candidates)


def test_displayed_times_are_ist_as_the_column_claims() -> None:
    """Regression: the table header says IST, so it must not print UTC.

    Slot times are UTC internally. An explanation shown to a merchant or an
    auditor that labels a UTC clock as IST is off by 5h30m and would appear to
    show debits inside NPCI's blackout windows.
    """
    scenario = _failed_cycle()
    _, candidates = _ranked(scenario)
    text = format_explanation(candidates, top=3)

    winner = next(c for c in candidates if c.chosen)
    assert winner.when.astimezone(IST).strftime("%Y-%m-%d %H:%M") in text
    assert winner.when.strftime("%Y-%m-%d %H:%M") not in text, "printed UTC under an IST header"


def test_no_candidate_falls_outside_a_legal_execution_window() -> None:
    """Whatever the explanation offers must be executable (§1)."""
    adapter = UpiAutopayAdapter()
    _, candidates = _ranked(_failed_cycle())
    assert candidates, "scenario produced no candidates"
    offending = [c.slot for c in candidates if not adapter.is_execution_legal(c.when)]
    assert offending == [], f"slots outside the NPCI windows were offered: {offending}"
