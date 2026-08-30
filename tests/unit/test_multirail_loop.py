"""One engine, three rails, one set of metrics (§9; Phase 14's artifact).

**Phase 14 exit criterion:** "All three rails run the full loop end to end."

Asserted with rail-*specific* behaviour rather than "it ran". A loop that
produced identical output on all three rails would mean the abstraction was
carrying nothing — which is precisely what §9 warns about when it says eNACH is
"the rail that proves the abstraction".
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from prayas.domain.rails import adapter_for
from prayas.measure.harness import HORIZON_SLOTS, legal_mask
from prayas.sequencer.dp import solve
from prayas.sequencer.economics import attempt_cost_matrix

RAILS = ["upi_autopay", "card_emandate", "enach"]

# A Monday, so eNACH has a clearing cycle to present into.
DUE_AT = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
AMOUNT = 149_900
W = 1_200_000


def _policy(rail: str) -> object:
    """Run the sequencer end to end on one rail."""
    legal = legal_mask(DUE_AT, rail=rail)
    budget = adapter_for(rail).attempt_budget
    return solve(
        presence=np.full(HORIZON_SLOTS, 0.35),
        legal=legal,
        cost=attempt_cost_matrix(amount_paise=AMOUNT, budget=budget, horizon_slots=HORIZON_SLOTS),
        amount_paise=AMOUNT,
        continuation_value_paise=W,
        dr=np.full(HORIZON_SLOTS, 0.01),
        health=np.ones(HORIZON_SLOTS),
        budget=budget,
        lead_slots=25,
        p_recoverable=0.9,
    )


# ── the criterion ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("rail", RAILS)
def test_every_rail_completes_the_loop(rail: str) -> None:
    """**Phase 14 exit criterion.** Legal slots exist, the DP solves, and it
    chooses somewhere lawful to act."""
    legal = legal_mask(DUE_AT, rail=rail)
    assert legal.any(), f"{rail} has no lawful slot in the dunning window"

    policy = _policy(rail)
    chosen = policy.best_slot(adapter_for(rail).attempt_budget, 0)  # type: ignore[attr-defined]
    assert chosen is not None, f"{rail} produced no action"
    assert legal[int(chosen)], f"{rail} chose an unlawful slot"


@pytest.mark.parametrize("rail", RAILS)
def test_every_rail_respects_its_own_budget(rail: str) -> None:
    adapter = adapter_for(rail)
    policy = _policy(rail)

    slots, remaining, t = [], adapter.attempt_budget, 0
    while remaining > 0:
        nxt = policy.best_slot(remaining, t)  # type: ignore[attr-defined]
        if nxt is None:
            break
        slots.append(int(nxt))
        remaining -= 1
        t = int(nxt)

    assert len(slots) <= adapter.attempt_budget
    assert slots == sorted(slots)


# ── and they are genuinely different ───────────────────────────────────────


def test_the_rails_do_not_all_look_the_same() -> None:
    """If the loop produced identical lawful windows on all three, the rail
    abstraction would be carrying nothing."""
    masks = {rail: legal_mask(DUE_AT, rail=rail) for rail in RAILS}

    assert not np.array_equal(masks["upi_autopay"], masks["card_emandate"])
    assert not np.array_equal(masks["card_emandate"], masks["enach"])
    assert not np.array_equal(masks["upi_autopay"], masks["enach"])


def test_card_has_the_most_lawful_slots_and_enach_the_fewest() -> None:
    """§9: card is "continuous", UPI is non-peak only, eNACH is
    clearing-cycle bound — the tightest of the three."""
    counts = {rail: int(legal_mask(DUE_AT, rail=rail).sum()) for rail in RAILS}

    assert counts["card_emandate"] > counts["upi_autopay"] > counts["enach"] > 0, counts


def test_enach_only_offers_pre_cutoff_weekday_slots() -> None:
    """The batch rail's windows are not a schedule, they are a clearing
    calendar. Every lawful slot must be a weekday before the cut-off."""
    adapter = adapter_for("enach")
    legal = legal_mask(DUE_AT, rail="enach")

    for slot in np.flatnonzero(legal).tolist():
        at = DUE_AT + np.timedelta64(int(slot), "h").astype("timedelta64[s]").item()
        assert adapter.is_execution_legal(DUE_AT + (at - DUE_AT))


def test_the_notice_lead_holds_on_every_rail() -> None:
    """§30.1's rule is rail-agnostic, so no rail may offer a slot inside it."""
    for rail in RAILS:
        legal = legal_mask(DUE_AT, rail=rail)
        assert not legal[:25].any(), f"{rail} offered a slot inside the notice lead"


def test_the_default_rail_is_unchanged() -> None:
    """Every phase before 14 measured on UPI Autopay. The default must still
    produce exactly that, or recorded numbers would silently move."""
    assert np.array_equal(legal_mask(DUE_AT), legal_mask(DUE_AT, rail="upi_autopay"))
