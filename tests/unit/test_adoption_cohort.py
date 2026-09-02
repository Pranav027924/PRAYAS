"""Cohort assignment (ADR-095; Master Spec §44).

The properties here are the ones a wrong implementation would still appear to
satisfy on casual inspection: proportions that look right on average but drift
between calls, or a holdout that is a biased slice rather than a random one.
"""

from __future__ import annotations

import pytest

from prayas.adoption.cohort import Arm, arm_for, may_act_on
from prayas.adoption.stages import BEHAVIOUR, Stage

TENANT = "t_cohort"
MANDATES = [f"sub_{i}" for i in range(20_000)]


def _counts(stage: Stage, tenant: str = TENANT) -> dict[Arm, int]:
    counts = dict.fromkeys(Arm, 0)
    for m in MANDATES:
        counts[arm_for(stage, tenant_id=tenant, mandate_id=m)] += 1
    return counts


def test_observe_and_shadow_treat_nothing() -> None:
    """§44: neither stage has a rollout, so no mandate may be acted on."""
    for stage in (Stage.OBSERVE, Stage.SHADOW):
        assert BEHAVIOUR[stage].mandate_share == 0.0
        assert all(
            arm_for(stage, tenant_id=TENANT, mandate_id=m) is Arm.EXCLUDED for m in MANDATES[:500]
        )


@pytest.mark.parametrize("stage", [Stage.CANARY, Stage.RAMP, Stage.FULL])
def test_treated_share_matches_the_stage(stage: Stage) -> None:
    """The share is the safety property: a canary that treats everything is not one."""
    counts = _counts(stage)
    inside = counts[Arm.TREATMENT] + counts[Arm.HOLDOUT]
    observed = inside / len(MANDATES)
    expected = BEHAVIOUR[stage].mandate_share
    # 20k draws; 3 sd of a binomial proportion is well inside 0.01 even at p=0.5.
    assert abs(observed - expected) < 0.01, f"{stage.name}: {observed:.4f} vs {expected}"


@pytest.mark.parametrize("stage", [Stage.RAMP, Stage.FULL])
def test_holdout_is_a_fraction_of_the_treated_share(stage: Stage) -> None:
    """§44: '10% of portfolio, 15% holdout inside it' — inside, not of the whole."""
    counts = _counts(stage)
    inside = counts[Arm.TREATMENT] + counts[Arm.HOLDOUT]
    observed = counts[Arm.HOLDOUT] / inside
    assert abs(observed - BEHAVIOUR[stage].holdout_pct) < 0.02


def test_canary_has_no_holdout() -> None:
    """§44 gives CANARY treatment only; a 15% split of 1% concludes nothing."""
    assert _counts(Stage.CANARY)[Arm.HOLDOUT] == 0


def test_full_never_retires_its_holdout() -> None:
    """The holdout is how uplift stays measurable, so FULL keeps one."""
    assert BEHAVIOUR[Stage.FULL].holdout_pct > 0
    assert _counts(Stage.FULL)[Arm.HOLDOUT] > 0


def test_assignment_is_stable_across_calls() -> None:
    """A mandate that drifts between arms destroys the comparison it exists for."""
    first = [arm_for(Stage.FULL, tenant_id=TENANT, mandate_id=m) for m in MANDATES[:2000]]
    second = [arm_for(Stage.FULL, tenant_id=TENANT, mandate_id=m) for m in MANDATES[:2000]]
    assert first == second


def test_ramping_adds_mandates_rather_than_reshuffling_them() -> None:
    """A mandate treated at CANARY must still be inside the rollout at RAMP.

    Otherwise advancing a stage silently swaps the population, and the
    before/after the ramp exists to produce compares two different things.
    """
    for m in MANDATES:
        if arm_for(Stage.CANARY, tenant_id=TENANT, mandate_id=m) is not Arm.EXCLUDED:
            assert arm_for(Stage.RAMP, tenant_id=TENANT, mandate_id=m) is not Arm.EXCLUDED
            assert arm_for(Stage.FULL, tenant_id=TENANT, mandate_id=m) is not Arm.EXCLUDED


def test_tenants_are_assigned_independently() -> None:
    """One tenant's arm must not predict another's for the same mandate id."""
    agree = sum(
        arm_for(Stage.FULL, tenant_id="t_a", mandate_id=m)
        is arm_for(Stage.FULL, tenant_id="t_b", mandate_id=m)
        for m in MANDATES[:5000]
    )
    # Identical assignment would give 5000. Independent draws at 15% holdout
    # agree ~74.5% of the time; anything near-perfect means the salt is inert.
    assert agree < 4_500


def test_holdout_is_independent_of_the_share_draw() -> None:
    """Two salts, so the holdout is not a biased slice of the share's ordering.

    If both draws came from one hash, the holdout would always sit at one end
    of the treated share — a control arm systematically unlike the treatment.
    Bucketing the treated mandates by their position within the share and
    checking the holdout rate is roughly flat across buckets catches that.
    """
    buckets: list[list[int]] = [[] for _ in range(5)]
    for m in MANDATES:
        arm = arm_for(Stage.FULL, tenant_id=TENANT, mandate_id=m)
        if arm is Arm.EXCLUDED:
            continue
        from prayas.adoption.cohort import _SHARE_SALT, _unit_interval

        draw = _unit_interval(tenant_id=TENANT, mandate_id=m, salt=_SHARE_SALT)
        buckets[min(int(draw * 5), 4)].append(1 if arm is Arm.HOLDOUT else 0)

    rates = [sum(b) / len(b) for b in buckets if b]
    assert len(rates) == 5
    assert max(rates) - min(rates) < 0.05, f"holdout rate varies across share buckets: {rates}"


def test_may_act_on_agrees_with_the_arm() -> None:
    for m in MANDATES[:1000]:
        acted = may_act_on(Stage.FULL, tenant_id=TENANT, mandate_id=m)
        assert acted is (arm_for(Stage.FULL, tenant_id=TENANT, mandate_id=m) is Arm.TREATMENT)
