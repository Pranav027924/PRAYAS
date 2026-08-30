"""Phase 10's remaining criteria: date change (§24.3) and back off (§24.6)."""

from __future__ import annotations

import pytest

from prayas.retention.interventions import (
    AT_RISK_THRESHOLD,
    CHRONIC_CYCLES,
    DATE_CHANGE_MIN_LIFT,
    UNAVAILABLE,
    DayOfMonthHazard,
    Decision,
    Intervention,
    InterventionError,
    day_of_month_hazard,
    propose_date_change,
    select,
)
from prayas.retention.revocation import RevocationFeatures, RevocationModel
from prayas.sim.config import SimConfig
from prayas.sim.lifecycle import flatten, generate_lifecycles

AMOUNT = 49_900
W = AMOUNT * 5


def _hazard(p_by_day: dict[int, float], *, observations: int = 200) -> DayOfMonthHazard:
    return DayOfMonthHazard(p_by_day=p_by_day, observations=dict.fromkeys(p_by_day, observations))


def _model(monthly: float = 0.05) -> RevocationModel:
    return RevocationModel(cell_hazard={}, cell_counts={}, global_hazard=monthly)


def _features() -> RevocationFeatures:
    return RevocationFeatures(
        consecutive_failures=3, days_since_success=90.0, successful_cycles=2, rail="upi_autopay"
    )


# ── Exit criterion: date change fires only when chronic AND materially better ──


def test_date_change_requires_both_a_material_lift_and_chronicity() -> None:
    """§24.3: `if lift > 0.25 and chronic`. Both, not either.

    A large lift on a mandate that failed once is noise; a chronic mandate with
    a small lift is not worth the amendment and the consent it costs.
    """
    generous = _hazard({1: 0.30, 5: 0.90})  # lift 0.60, well over the threshold

    assert (
        propose_date_change(
            debit_day=1, hazard=generous, consecutive_high_risk_cycles=CHRONIC_CYCLES
        )
        is not None
    )

    # Material lift, but not chronic.
    assert (
        propose_date_change(
            debit_day=1, hazard=generous, consecutive_high_risk_cycles=CHRONIC_CYCLES - 1
        )
        is None
    )

    # Chronic, but the lift is immaterial.
    marginal = _hazard({1: 0.60, 5: 0.70})  # lift 0.10
    assert (
        propose_date_change(
            debit_day=1, hazard=marginal, consecutive_high_risk_cycles=CHRONIC_CYCLES
        )
        is None
    )


@pytest.mark.parametrize(
    ("lift", "fires"),
    [
        (DATE_CHANGE_MIN_LIFT - 0.01, False),
        (DATE_CHANGE_MIN_LIFT, False),
        (DATE_CHANGE_MIN_LIFT + 0.01, True),
    ],
)
def test_the_lift_threshold_is_strict(lift: float, fires: bool) -> None:
    """§24.3 writes `>`, not `>=` — exactly at 0.25 does not fire."""
    hazard = _hazard({1: 0.40, 5: 0.40 + lift})
    proposal = propose_date_change(
        debit_day=1, hazard=hazard, consecutive_high_risk_cycles=CHRONIC_CYCLES
    )
    assert (proposal is not None) is fires


@pytest.mark.parametrize(("cycles", "fires"), [(CHRONIC_CYCLES - 1, False), (CHRONIC_CYCLES, True)])
def test_the_chronic_threshold_is_inclusive(cycles: int, fires: bool) -> None:
    """§24.3 writes `>= 3`."""
    hazard = _hazard({1: 0.20, 5: 0.90})
    proposal = propose_date_change(debit_day=1, hazard=hazard, consecutive_high_risk_cycles=cycles)
    assert (proposal is not None) is fires


def test_no_proposal_when_the_mandate_already_debits_on_the_best_day() -> None:
    """§24.3: `if best_dom == current: return None`."""
    hazard = _hazard({1: 0.20, 5: 0.90})
    assert propose_date_change(debit_day=5, hazard=hazard, consecutive_high_risk_cycles=9) is None


def test_a_thinly_observed_day_cannot_produce_a_lift() -> None:
    """§27's floor: acting on a handful of cycles would invent the lift."""
    hazard = DayOfMonthHazard(p_by_day={1: 0.30, 5: 0.99}, observations={1: 500, 5: 2})
    assert hazard.peak_day_of_month() == 1, "a 2-observation day was treated as the peak"
    assert propose_date_change(debit_day=1, hazard=hazard, consecutive_high_risk_cycles=9) is None


def test_the_proposal_explains_itself() -> None:
    """It requires consent, so a human has to be able to read the reason."""
    proposal = propose_date_change(
        debit_day=1, hazard=_hazard({1: 0.25, 5: 0.85}), consecutive_high_risk_cycles=4
    )
    assert proposal is not None
    assert proposal.from_day == 1 and proposal.to_day == 5
    assert "debit day 1 -> 5" in proposal.rationale
    assert "60.0%" in proposal.rationale


def test_day_of_month_hazard_fits_from_observed_cycles() -> None:
    """The curve §24.3 compares days against, built from real cycles."""
    lifecycles = generate_lifecycles(
        SimConfig(cycles_per_mandate=6), seed=4242, tenant_id="t_dom", mandates=1500
    )
    hazard = day_of_month_hazard(flatten(lifecycles))

    assert hazard.p_by_day, "no days observed"
    peak = hazard.peak_day_of_month()
    assert 1 <= peak <= 31
    assert 0.0 <= hazard.p_funded(peak) <= 1.0
    with pytest.raises(InterventionError, match="1-31"):
        hazard.p_funded(0)


# ── Exit criterion: back-off is chosen and carries a rationale ──────────────


def _select(**overrides: object) -> Decision:
    kwargs = {
        "hard_stop_reasons": [],
        "debit_day": 5,
        "hazard": _hazard({5: 0.60}),
        "consecutive_high_risk_cycles": 0,
        "revocation": _model(),
        "features": _features(),
        "best_attempt_ev_paise": float(W) - 1000.0,
        "continuation_value_paise": W,
        "messages_30d": 0,
        "fatigue_cap": 3,
    }
    kwargs.update(overrides)
    return select(**kwargs)  # type: ignore[arg-type]


def test_back_off_is_selected_when_no_action_beats_leaving_the_mandate_alone() -> None:
    """§24.6: "do nothing, and record that doing nothing was chosen and why"."""
    decision = _select()
    assert decision.intervention is Intervention.BACK_OFF
    assert decision.is_back_off
    assert "backed off" in decision.rationale
    # Rupee-denominated, like a stopping rationale (§23.3).
    assert "Rs" in decision.rationale


def test_back_off_names_the_revocation_hazard_when_it_is_elevated() -> None:
    """§24.6: "revocation hazard rising" is part of the reason, so say it."""
    decision = _select(revocation=_model(monthly=AT_RISK_THRESHOLD + 0.05))
    assert decision.is_back_off
    assert "revocation hazard" in decision.rationale
    assert "at-risk threshold" in decision.rationale


def test_back_off_names_fatigue_when_that_is_the_binding_reason() -> None:
    """§24.6: "fatigue high" — a system that cannot select silence over-messages."""
    decision = _select(messages_30d=3, fatigue_cap=3)
    assert decision.is_back_off
    assert "fatigue" in decision.rationale


def test_retry_is_chosen_when_it_beats_holding_the_mandate() -> None:
    """The selector must be able to act, or backing off proves nothing."""
    decision = _select(best_attempt_ev_paise=float(W) + 50_000.0)
    assert decision.intervention is Intervention.RETRY
    assert "exceeds" in decision.rationale


# ── §23.4: hard overrides run before economics ─────────────────────────────


def test_a_hard_stop_wins_over_an_attractive_attempt() -> None:
    """§23.4: "an economic argument must never override a legal one".

    The attempt is worth far more than the mandate, and the date change would
    also qualify — neither may be reached.
    """
    decision = _select(
        hard_stop_reasons=["mandate is not active"],
        best_attempt_ev_paise=float(W) * 100,
        hazard=_hazard({1: 0.10, 5: 0.99}),
        debit_day=1,
        consecutive_high_risk_cycles=9,
    )
    assert decision.hard_stopped
    assert decision.intervention is Intervention.BACK_OFF
    assert "hard stop" in decision.rationale
    assert decision.date_change is None


def test_the_permanent_fix_is_preferred_over_retrying() -> None:
    """§24.3: it "eliminates the failure permanently rather than recovering
    from it monthly", so it outranks a retry that would also have fired."""
    decision = _select(
        debit_day=1,
        hazard=_hazard({1: 0.20, 5: 0.90}),
        consecutive_high_risk_cycles=4,
        best_attempt_ev_paise=float(W) + 50_000.0,
    )
    assert decision.intervention is Intervention.DATE_CHANGE
    assert decision.date_change is not None


def test_unavailable_interventions_are_named_not_hidden() -> None:
    """§24.5 exists in the catalogue but cannot yet be selected.

    §24.4 used to be listed here too, deferred with the reason "card e-mandate
    rail arrives in Phase 14". The rail arrived, so the deferral left with it —
    which is why the reason was written down rather than left implicit. Its
    behaviour is covered in `test_rail_migration.py`.
    """
    assert Intervention.RAIL_MIGRATION not in UNAVAILABLE
    assert Intervention.PARTIAL_COLLECTION in UNAVAILABLE
    assert "above-AFA-cap" in UNAVAILABLE[Intervention.PARTIAL_COLLECTION]
    assert len(list(Intervention)) == 6, "§24 names six interventions"
