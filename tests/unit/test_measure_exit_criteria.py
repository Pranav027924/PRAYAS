"""Phase 7 exit criteria that need no database.

The A/A, SRM and CUPED criteria are properties of the estimators and the
assignment function, so they are tested directly rather than through a batch
run — thousands of replications are the point, and each one costing a database
round-trip would make the coverage claim unaffordable.
"""

from __future__ import annotations

import numpy as np
import pytest

from prayas.measure.assignment import CONTROL, TREATMENT, arm, assign_many, propensity
from prayas.measure.estimators import ArmSummary, cuped, incremental, srm_check
from prayas.measure.guardrails import (
    NetValue,
    complaint_rate,
    compliance_violations,
    message_volume,
    net_value_guardrail,
    revocation_rate,
)

# ── Exit criterion: A/A test, incremental lift CI contains zero ─────────────


def test_aa_test_intervals_cover_zero_at_the_nominal_rate() -> None:
    """A single A/A passing proves almost nothing — coverage is the claim.

    Both arms are drawn from one population, so the true lift is exactly zero.
    A correct 95% interval should contain zero about 95% of the time. Asserting
    one lucky replication would pass even for a badly miscalibrated interval;
    asserting the *rate* is what actually tests the estimator.
    """
    rng = np.random.default_rng(20260827)
    true_rate, n, replications = 0.28, 4000, 600

    covered = 0
    for _ in range(replications):
        a = int(rng.binomial(n, true_rate))
        b = int(rng.binomial(n, true_rate))
        result = incremental(ArmSummary(n=n, recovered=a), ArmSummary(n=n, recovered=b))
        covered += result.contains_zero

    rate = covered / replications
    # Binomial noise on 600 draws at p=0.95 has sd ~0.9pp; ±3pp is ~3 sd.
    assert 0.92 <= rate <= 0.98, f"A/A coverage was {rate:.3f}, expected ~0.95"


def test_aa_test_finds_no_significant_effect_on_a_single_large_sample() -> None:
    """The plain reading of the criterion, on one well-powered replication."""
    rng = np.random.default_rng(7)
    n, rate = 20_000, 0.30
    result = incremental(
        ArmSummary(n=n, recovered=int(rng.binomial(n, rate))),
        ArmSummary(n=n, recovered=int(rng.binomial(n, rate))),
    )
    assert result.contains_zero, f"A/A produced a significant lift: {result}"


def test_a_real_effect_is_still_detected() -> None:
    """Guards the A/A: an estimator that always contains zero would pass it."""
    n = 20_000
    result = incremental(
        ArmSummary(n=n, recovered=int(0.34 * n)), ArmSummary(n=n, recovered=int(0.28 * n))
    )
    assert result.excludes_zero, "a 6pp effect at n=20,000 must be detectable"
    assert result.point == pytest.approx(0.06, abs=0.001)


# ── Exit criterion: SRM passes across 100 seeds ─────────────────────────────


def test_srm_passes_across_one_hundred_seeds() -> None:
    """§33's assignment must split at the registered ratio, seed after seed.

    Uses the real `arm()` function on a real population rather than simulated
    counts — the criterion is about the assignment, not about the chi-square.
    """
    control_pct = 0.10
    population = [f"cust_{i}" for i in range(6000)]

    failures = []
    for seed_n in range(100):
        assignment = assign_many(population, f"seed_{seed_n}", control_pct)
        n_control = sum(1 for a in assignment.values() if a == CONTROL)
        result = srm_check(n_control, len(population) - n_control, control_pct)
        if not result.passed:
            failures.append((seed_n, n_control, result.p_value))

    assert failures == [], f"SRM failed on seeds: {failures}"


def test_srm_detects_a_broken_assignment() -> None:
    """The check must be able to fail, or 100 passes mean nothing.

    Simulates the classic bug: assignment silently uses the wrong ratio.
    """
    population = [f"cust_{i}" for i in range(6000)]
    assignment = assign_many(population, "seed_0", 0.30)  # actually 30% control
    n_control = sum(1 for a in assignment.values() if a == CONTROL)

    result = srm_check(n_control, len(population) - n_control, 0.10)  # registered 10%
    assert not result.passed, "a 30/10 mismatch was not detected"


def test_assignment_is_deterministic_and_customer_level() -> None:
    """§33: no assignment table, and randomisation keyed on the customer.

    The same customer must land in the same arm on every call and from every
    call site — otherwise a customer straddles arms and contaminates both.
    """
    seed, pct = "committed_seed", 0.2
    assert {arm("cust_a", seed, pct) for _ in range(100)} == {arm("cust_a", seed, pct)}

    # A different seed is a different experiment, so arms may differ.
    arms_by_seed = {arm("cust_a", f"s{i}", pct) for i in range(50)}
    assert arms_by_seed == {CONTROL, TREATMENT}, "assignment ignores the seed"


def test_propensity_matches_the_arm_that_was_assigned() -> None:
    """ADR-048: P(the unit received the arm it received)."""
    assert propensity(CONTROL, 0.1) == pytest.approx(0.1)
    assert propensity(TREATMENT, 0.1) == pytest.approx(0.9)


# ── Exit criterion: CUPED demonstrably reduces variance ─────────────────────


def test_cuped_reduces_variance_on_simulated_recovery_data() -> None:
    """With a genuine pre-period correlation, the interval should narrow.

    Modelled the way the real covariate behaves: a customer's recovery
    propensity persists across cycles, so their pre-period rate predicts their
    in-experiment outcome.
    """
    rng = np.random.default_rng(4242)
    n = 5000

    # Per-customer latent propensity, observed noisily in both periods.
    latent = rng.beta(3, 6, size=n)
    pre_period = latent + rng.normal(0, 0.05, size=n)
    outcome = (rng.random(n) < latent).astype(np.float64)

    adjusted = cuped(outcome, pre_period)

    raw_var = float(np.var(outcome))
    adj_var = float(np.var(adjusted))
    reduction = 1.0 - adj_var / raw_var

    assert reduction > 0.05, f"CUPED reduced variance by only {reduction:.1%}"
    # And it must not have moved the estimate — that would be bias, not gain.
    assert float(np.mean(adjusted)) == pytest.approx(float(np.mean(outcome)), abs=1e-9)


def test_cuped_gains_nothing_from_an_unrelated_covariate() -> None:
    """A covariate with no signal must not appear to help.

    If CUPED "reduced variance" against noise, it would be fitting the noise —
    and the narrower interval would be a false claim of precision.
    """
    rng = np.random.default_rng(99)
    n = 5000
    outcome = (rng.random(n) < 0.3).astype(np.float64)
    unrelated = rng.normal(size=n)

    reduction = 1.0 - float(np.var(cuped(outcome, unrelated))) / float(np.var(outcome))
    assert reduction < 0.01, f"CUPED claimed {reduction:.1%} from an unrelated covariate"


# ── Exit criterion: guardrails compute correctly, including unflattering ────


def test_every_guardrail_can_fire() -> None:
    """§6's guardrails, each fed data that should trip it.

    A guardrail that never fires is indistinguishable from a broken one, so
    each is asserted to breach on bad input *and* pass on good.
    """
    assert compliance_violations(1).breached
    assert not compliance_violations(0).breached

    assert revocation_rate(treatment=0.09, control=0.07).breached
    assert not revocation_rate(treatment=0.06, control=0.07).breached

    assert message_volume(4.2, fatigue_cap=3.0).breached
    assert not message_volume(2.1, fatigue_cap=3.0).breached

    assert complaint_rate(treatment=0.02, control=0.01).breached
    assert not complaint_rate(treatment=0.005, control=0.01).breached


def test_net_value_guardrail_breaches_when_churn_eats_the_recovery() -> None:
    """§35's whole point, as an assertion.

    Recovery looks strongly positive; the induced churn more than cancels it.
    A system reporting recovery alone would claim a win here.
    """
    value = NetValue(
        incremental_recovered_paise=5_000_00,
        incremental_retained_ltv_paise=0,
        attempt_fees_paise=20_00,
        messaging_cost_paise=10_00,
        ltv_lost_to_churn_paise=6_000_00,
    )
    guard = net_value_guardrail(value)

    assert guard.breached, "net value was negative but the guardrail passed"
    assert value.recovery_only_paise > 0, "recovery alone would have looked positive"
    assert value.overstatement_paise > 0
    assert "overstating by" in guard.detail


def test_net_value_passes_when_retention_holds() -> None:
    value = NetValue(
        incremental_recovered_paise=5_000_00,
        incremental_retained_ltv_paise=2_000_00,
        attempt_fees_paise=20_00,
        messaging_cost_paise=10_00,
        ltv_lost_to_churn_paise=100_00,
    )
    assert not net_value_guardrail(value).breached
    assert value.total_paise == 5_000_00 + 2_000_00 - 20_00 - 10_00 - 100_00
