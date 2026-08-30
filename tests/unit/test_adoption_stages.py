"""The adoption ramp, gated in code (Master Spec §44; Phase 17).

**Phase 17 exit criterion:** "Stage transitions gated by their exit criteria in
code, not in a document."

§44 says the ramp "is a product feature, not a rollout plan." The difference
this file defends is that a gate in a wiki is a gate somebody advances on a
Friday because the meeting went well.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from prayas.adoption.stages import (
    BEHAVIOUR,
    GATES,
    AdoptionError,
    Evidence,
    Stage,
    advance,
    behaviour,
    can_advance,
    may_decide,
    may_fire,
)

#: Evidence that satisfies every gate, so a test can fail one criterion at a time.
COMPLETE = Evidence(
    event_completeness=0.9995,
    projection_matches_days=7,
    shadow_decisions=10_000,
    gate_errors=0,
    calibration_ece=0.004,
    audit_report_produced=True,
    canary_days=14,
    double_debits=0,
    compliance_violations=0,
    revocation_rate_above_baseline=False,
    recovery_ci_excludes_zero=True,
    survival_ci_negative=False,
    guardrails_green_days=30,
)


# ── §44's behaviour column ─────────────────────────────────────────────────


def test_observe_ingests_only() -> None:
    """§44: "Ingest only. No decisions.\""""
    assert not may_decide(Stage.OBSERVE)
    assert not may_fire(Stage.OBSERVE)


def test_shadow_decides_and_fires_nothing() -> None:
    """§44: "Decide and log. **Fire nothing.**" The emphasis is the spec's."""
    assert may_decide(Stage.SHADOW)
    assert not may_fire(Stage.SHADOW)


@pytest.mark.parametrize("stage", [Stage.CANARY, Stage.RAMP, Stage.FULL])
def test_only_the_later_stages_fire(stage: Stage) -> None:
    assert may_fire(stage)


def test_the_canary_is_one_percent_with_no_holdout() -> None:
    """§44: "1% of mandates, treatment only, no holdout." Splitting a 1% canary
    further would leave a control arm too thin to conclude anything from, and
    this stage is about safety rather than measurement."""
    canary = behaviour(Stage.CANARY)
    assert canary.mandate_share == 0.01
    assert canary.holdout_pct == 0.0


def test_the_ramp_carries_a_holdout_inside_it() -> None:
    ramp = behaviour(Stage.RAMP)
    assert ramp.mandate_share == 0.10
    assert ramp.holdout_pct == 0.15


def test_the_holdout_is_never_retired() -> None:
    """§44: "The holdout is never retired. It is how the system continues to
    know it is working, and it is what makes any value claim renewable rather
    than a one-time measurement."

    Asserted at FULL specifically, because that is the stage where retiring it
    would be tempting and where doing so would silently end the measurement.
    """
    full = behaviour(Stage.FULL)
    assert full.mandate_share == 1.0
    assert full.holdout_pct > 0.0, "the holdout was retired at full rollout"


def test_every_stage_has_a_declared_behaviour() -> None:
    assert set(BEHAVIOUR) == set(Stage)


# ── the criterion: gates are predicates, not prose ─────────────────────────


def test_a_full_ramp_advances_through_every_stage() -> None:
    """**Phase 17 exit criterion.** With complete evidence, each gate opens."""
    stage = Stage.OBSERVE
    for expected in (Stage.SHADOW, Stage.CANARY, Stage.RAMP, Stage.FULL):
        stage = advance(stage, COMPLETE)
        assert stage is expected


def test_the_final_stage_has_nowhere_to_go() -> None:
    assert Stage.FULL not in GATES
    with pytest.raises(AdoptionError, match="final stage"):
        advance(Stage.FULL, COMPLETE)


@pytest.mark.parametrize(
    ("stage", "field", "failing_value", "expected"),
    [
        (Stage.OBSERVE, "event_completeness", 0.998, "event completeness"),
        (Stage.OBSERVE, "projection_matches_days", 6, "projection matched"),
        (Stage.SHADOW, "shadow_decisions", 9_999, "shadow decisions"),
        (Stage.SHADOW, "gate_errors", 1, "gate errors"),
        (Stage.SHADOW, "calibration_ece", 0.05, "calibration ECE"),
        (Stage.SHADOW, "audit_report_produced", False, "audit report"),
        (Stage.CANARY, "canary_days", 13, "canary days"),
        (Stage.CANARY, "double_debits", 1, "double debits"),
        (Stage.CANARY, "compliance_violations", 1, "compliance violations"),
        (Stage.CANARY, "revocation_rate_above_baseline", True, "revocation rate"),
        (Stage.RAMP, "recovery_ci_excludes_zero", False, "recovery CI"),
        (Stage.RAMP, "survival_ci_negative", True, "survival CI"),
        (Stage.RAMP, "guardrails_green_days", 29, "guardrails green"),
    ],
)
def test_each_criterion_blocks_on_its_own(
    stage: Stage, field: str, failing_value: Any, expected: str
) -> None:
    """Every cell of §44's exit-criteria column, failed one at a time.

    Testing them together would let one criterion silently stop being checked
    while the others carried the assertion.
    """
    evidence = replace(COMPLETE, **{field: failing_value})
    result = can_advance(stage, evidence)

    assert not result, f"{field} did not block {stage.name}"
    assert any(expected in reason for reason in result.unmet), result.unmet


def test_a_refusal_names_what_is_missing() -> None:
    """ "Not yet" without a reason is how a gate becomes something people route
    around."""
    result = can_advance(Stage.SHADOW, Evidence())
    assert not result
    assert len(result.unmet) == 3, result.unmet
    with pytest.raises(AdoptionError) as raised:
        advance(Stage.SHADOW, Evidence())
    assert "shadow decisions" in str(raised.value)


def test_a_count_of_bad_things_only_counts_once_volume_proves_it_was_measured() -> None:
    """The subtle half of "empty evidence proves nothing".

    `gate_errors=0` is the *passing* value, so unlike `shadow_decisions` it
    cannot default to unproven — an int has no "not yet looked" state. What
    stops "we saw no errors because we never ran" from passing is the
    *volume* criterion beside it: `shadow_decisions=0` blocks first, so zero
    gate errors is only ever read across ten thousand decisions.

    The same pairing protects the canary: `canary_days=0` blocks before zero
    double debits can be mistaken for evidence.
    """
    # Zero errors alone does not open the gate.
    assert not can_advance(Stage.SHADOW, Evidence(gate_errors=0))
    assert not can_advance(Stage.CANARY, Evidence(double_debits=0))

    # It is the volume criterion that makes the count meaningful.
    unmet = can_advance(Stage.SHADOW, Evidence(gate_errors=0)).unmet
    assert any("shadow decisions" in reason for reason in unmet)

    unmet = can_advance(Stage.CANARY, Evidence(double_debits=0)).unmet
    assert any("canary days" in reason for reason in unmet)


def test_empty_evidence_proves_nothing() -> None:
    """Defaults are the *unproven* value in every field, so evidence that was
    never gathered cannot be mistaken for evidence that passed."""
    for stage in (Stage.OBSERVE, Stage.SHADOW, Stage.CANARY, Stage.RAMP):
        assert not can_advance(stage, Evidence())


def test_stages_cannot_be_skipped() -> None:
    """§44's stages are sequential because each one's exit criteria are
    evidence gathered *during* the stage before it: you cannot demonstrate 14
    clean canary days without having run a canary."""
    assert advance(Stage.OBSERVE, COMPLETE) is Stage.SHADOW
    assert Stage.OBSERVE + 1 == Stage.SHADOW


def test_the_zero_budget_criteria_admit_no_tolerance() -> None:
    """§42 gives double debits and compliance violations no error budget:
    "any occurrence is a Sev-1". One is not "close enough to zero"."""
    assert not can_advance(Stage.CANARY, replace(COMPLETE, double_debits=1))
    assert not can_advance(Stage.CANARY, replace(COMPLETE, compliance_violations=1))
