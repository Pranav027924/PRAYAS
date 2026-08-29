"""Δr(t), issuer health, and funding windows (ADR-072, ADR-073; §21, §22, §23).

These pin the two properties FINDING-P11-01 turned on: that the economics the
sequencer consumes actually vary with time and with the mandate, and that the
absorbing default is unchanged so no closed phase's number moves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from prayas.inference.nowcast import IssuerNowcast
from prayas.measure.harness import (
    HORIZON_SLOTS,
    funding_slot,
    funds_present,
    revocation_features,
)
from prayas.retention import revocation
from prayas.retention.revocation import RevocationFeatures
from prayas.sequencer.economics import (
    DELTA_R_PER_ATTEMPT,
    HEALTH_NEUTRAL,
    OUTAGE_HEALTH,
    health_multiplier,
    revocation_delta,
)
from prayas.sim.config import SimConfig
from prayas.sim.generate import generate
from prayas.sim.lifecycle import generate_lifecycles

TENANT = "t_econ"
T0 = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def model() -> revocation.RevocationModel:
    return revocation.fit(
        generate_lifecycles(
            SimConfig(cycles_per_mandate=8), seed=31, tenant_id=TENANT, mandates=1500
        )
    )


# ── Δr ─────────────────────────────────────────────────────────────────────


def test_without_a_model_delta_r_is_adr_037s_constant() -> None:
    """Backward compatibility is the whole point of the fallback."""
    dr = revocation_delta(HORIZON_SLOTS)
    assert np.all(dr == DELTA_R_PER_ATTEMPT)


def test_fitted_delta_r_rises_with_the_delay(model: revocation.RevocationModel) -> None:
    """The property that makes waiting cost something.

    Constant Δr plus absorbing funding means waiting is free, and the optimal
    policy is "attempt at the last legal slot" regardless of liquidity — which
    is exactly what FINDING-P11-01 measured.
    """
    features = RevocationFeatures(
        consecutive_failures=2, days_since_success=45.0, successful_cycles=2, rail="upi_autopay"
    )
    dr = revocation_delta(HORIZON_SLOTS, model=model, features=features)

    assert dr[0] == pytest.approx(0.0, abs=1e-9), "waiting zero days accrues no extra risk"
    assert np.all(np.diff(dr) >= -1e-12), "Δr must not fall as the delay grows"
    assert dr[-1] > dr[0], "Δr is flat — the model is not time-dependent"


def test_delta_r_is_larger_for_a_sicker_mandate(model: revocation.RevocationModel) -> None:
    """§22's features must actually separate mandates, or Δr is a constant
    array wearing a different name."""
    healthy = revocation_delta(
        HORIZON_SLOTS,
        model=model,
        features=RevocationFeatures(1, 30.0, 6, "upi_autopay"),
    )
    chronic = revocation_delta(
        HORIZON_SLOTS,
        model=model,
        features=RevocationFeatures(5, 150.0, 0, "upi_autopay"),
    )
    assert chronic[-1] > healthy[-1] * 2


# ── issuer health ──────────────────────────────────────────────────────────


def test_health_is_neutral_without_a_nowcast() -> None:
    assert np.all(health_multiplier(HORIZON_SLOTS) == HEALTH_NEUTRAL)


def test_health_is_neutral_when_the_nowcast_has_no_opinion() -> None:
    """Silence must not suppress an attempt any more than it should license one."""
    quiet = IssuerNowcast(baseline_success=0.86)
    health = health_multiplier(HORIZON_SLOTS, nowcast=quiet, issuer="HDFC", at=T0)
    assert np.all(health == HEALTH_NEUTRAL)


def test_a_degraded_issuer_suppresses_slots_until_its_recovery_eta() -> None:
    """§20: "Wait for recovery ETA". Suppressed, not zeroed — an outage raises
    the failure rate, it does not close the shutter."""
    nowcast = IssuerNowcast(baseline_success=0.86)
    rng = np.random.default_rng(0)
    for minute in range(90):
        at = T0 + timedelta(minutes=minute)
        down = minute >= 40
        for _ in range(8):
            ok = bool(rng.random() < (0.02 if down else 0.86))
            nowcast.observe("HDFC", at + timedelta(seconds=int(rng.integers(0, 60))), success=ok)
        nowcast.flush(at)

    at = T0 + timedelta(minutes=89)
    assert nowcast.state("HDFC", at).is_degraded

    health = health_multiplier(HORIZON_SLOTS, nowcast=nowcast, issuer="HDFC", at=at)
    assert health[0] == pytest.approx(OUTAGE_HEALTH)
    assert 0.0 < health[0] < HEALTH_NEUTRAL, "a suppressed slot must stay reachable"
    assert health[-1] == pytest.approx(HEALTH_NEUTRAL), "health must return after the ETA"


# ── §22 features from history ──────────────────────────────────────────────


def test_features_come_only_from_earlier_cycles() -> None:
    """Observable history, never the future."""
    cycles = generate(SimConfig(), seed=9, tenant_id=TENANT, cycles=400)
    owned = sorted(
        (c for c in cycles if c.customer_id == cycles[0].customer_id), key=lambda c: c.due_at
    )
    if len(owned) < 2:
        pytest.skip("customer holds too few cycles in this draw")

    target = owned[-1]
    features = revocation_features(target, owned[:-1])
    assert features.consecutive_failures >= 1
    assert features.successful_cycles <= len(owned) - 1
    assert features.rail == target.rail


def test_a_customer_with_no_history_is_not_treated_as_healthy() -> None:
    """Cold start must not read as "recently paid" — that would understate Δr
    for exactly the mandates least is known about."""
    cycles = generate(SimConfig(), seed=9, tenant_id=TENANT, cycles=50)
    features = revocation_features(cycles[0], [])
    assert features.successful_cycles == 0
    assert features.days_since_success > 30.0


# ── funding windows ────────────────────────────────────────────────────────


def test_absorbing_funding_is_byte_identical_to_the_old_rule() -> None:
    """ADR-073's blast radius, asserted rather than assumed: under the default
    the new window logic reduces to `slot >= funding_slot`."""
    cycles = generate(SimConfig(), seed=77, tenant_id=TENANT, cycles=600)
    for cycle in cycles:
        present = funds_present(cycle)
        slot = funding_slot(cycle)
        expected = np.zeros(HORIZON_SLOTS, dtype=bool)
        if slot is not None:
            expected[slot:] = True
        assert np.array_equal(present, expected), cycle.cycle_id


def test_non_absorbing_funding_can_run_out() -> None:
    """§21: "money arrives and is spent". Without this a retry can never miss,
    and no timing model can matter."""
    cycles = generate(
        SimConfig(non_absorbing=True, funds_dwell_hours=12.0),
        seed=77,
        tenant_id=TENANT,
        cycles=600,
    )
    with_windows = [c for c in cycles if c.truth.funding_windows]
    assert with_windows, "no cycle received a funding window"

    bounded = 0
    for cycle in with_windows:
        present = funds_present(cycle)
        if present.any() and not present[-1]:
            bounded += 1
    assert bounded > 0, "every window still runs to the horizon — funds never leave"


def test_non_absorbing_recovery_is_strictly_harder() -> None:
    """The measurable cost of §21's stated limitation."""
    absorbing = generate(SimConfig(), seed=77, tenant_id=TENANT, cycles=800)
    leaky = generate(SimConfig(non_absorbing=True), seed=77, tenant_id=TENANT, cycles=800)

    reachable_absorbing = sum(int(funds_present(c).sum()) for c in absorbing)
    reachable_leaky = sum(int(funds_present(c).sum()) for c in leaky)
    assert reachable_leaky < reachable_absorbing


def test_dwell_hours_must_be_positive() -> None:
    from prayas.sim.config import ConfigError

    with pytest.raises(ConfigError, match="funds_dwell_hours"):
        SimConfig(funds_dwell_hours=0.0)


# ── §21's leak parameter ───────────────────────────────────────────────────


def test_zero_leak_is_exactly_the_absorbing_curve() -> None:
    """The default must change nothing that has already been measured."""
    from prayas.inference.hazard import leaky_survival, survival

    rng = np.random.default_rng(0)
    hazards = rng.uniform(0.001, 0.05, size=200)
    assert np.array_equal(leaky_survival(hazards, leak_per_day=0.0), survival(hazards))


def test_a_leak_keeps_survival_monotone() -> None:
    """§23.2 requires it, and `dp._validate` refuses anything else. A leak that
    broke monotonicity would not be a conservative model — it would be an
    invalid input the DP rejects outright."""
    from prayas.inference.hazard import leaky_survival

    rng = np.random.default_rng(1)
    hazards = rng.uniform(0.001, 0.08, size=400)
    for leak in (0.1, 0.5, 2.0):
        s = leaky_survival(hazards, leak_per_day=leak)
        assert np.all(np.diff(s) <= 1e-12), f"leak {leak} broke monotonicity"
        assert np.all((s > 0.0) & (s <= 1.0))


def test_a_leak_discounts_distant_funding_more_than_near_funding() -> None:
    """The whole mechanism: money forecast for day two keeps its credit, money
    forecast for day six does not."""
    from prayas.inference.hazard import leaky_survival, survival

    early = np.full(720, 1e-6)
    early[24:36] = 0.25
    late = np.full(720, 1e-6)
    late[600:612] = 0.25

    plain_early = 1.0 - survival(early)[-1]
    plain_late = 1.0 - survival(late)[-1]
    assert plain_early == pytest.approx(plain_late, abs=0.02), "curves must start comparable"

    leaked_early = 1.0 - leaky_survival(early, leak_per_day=0.5)[-1]
    leaked_late = 1.0 - leaky_survival(late, leak_per_day=0.5)[-1]
    assert leaked_early > leaked_late * 2, "the leak did not discriminate by arrival time"


def test_a_negative_leak_is_refused() -> None:
    from prayas.inference.hazard import leaky_survival

    with pytest.raises(ValueError, match="non-negative"):
        leaky_survival(np.full(10, 0.01), leak_per_day=-1.0)


def test_the_true_leak_is_non_monotone_and_the_dp_refuses_it() -> None:
    """FINDING-P11-01's structural claim, as an executable fact.

    The quantity a sequencer actually wants is `P(funds present at t)`: sum over
    arrivals `u <= t`, weighted by how long the money survives the gap `t - u`.
    Under §21's "money arrives and is spent" that quantity *falls* once money
    leaves, so the survival array implied by it is non-monotone — and §23.2's
    DP rejects non-monotone survival by design.

    This is why `leaky_survival` discounts by elapsed time rather than by
    arrival age, and why that approximation is the best available without
    changing §23.2. If this test ever fails, the constraint has moved and the
    finding should be revisited.
    """
    from prayas.sequencer.dp import solve
    from prayas.sequencer.economics import attempt_cost_matrix

    horizon = 240
    hazards = np.full(horizon, 1e-6)
    hazards[40:52] = 0.30  # one payday arrival

    arrivals = hazards * np.concatenate(([1.0], np.cumprod(1.0 - hazards)[:-1]))
    dwell_hours = 36.0
    present = np.array(
        [
            sum(
                arrivals[u] * np.exp(-(t - u) / dwell_hours)
                for u in range(t + 1)
                if arrivals[u] > 1e-9
            )
            for t in range(horizon)
        ]
    )
    assert present.max() > 0.5, "the arrival mass did not materialise"
    assert present[-1] < present.max() * 0.5, "funds never actually leave"

    implied_survival = 1.0 - present
    assert np.any(np.diff(implied_survival) > 1e-9), "the implied survival is monotone after all"

    with pytest.raises(ValueError, match="monotone non-increasing"):
        solve(
            survival=implied_survival,
            legal=np.ones(horizon, dtype=bool),
            cost=attempt_cost_matrix(amount_paise=100_000, budget=3, horizon_slots=horizon),
            amount_paise=100_000,
            continuation_value_paise=1_200_000,
            dr=np.full(horizon, 0.01),
            health=np.ones(horizon),
            budget=3,
            lead_slots=25,
            p_recoverable=0.9,
        )


def test_under_absorbing_funding_maximal_patience_is_the_oracle() -> None:
    """FINDING-P11-01's central evidence, and the reason +0.00% lift is correct.

    The oracle for any policy with at least one attempt is "does *any* legal
    slot hold money". Under absorbing funding, firing at the last legal slot
    attains it exactly — so there is no headroom for a liquidity model to
    exploit, and V1 failing to beat V0 end to end is the right answer rather
    than a weak model.
    """
    from prayas.measure.harness import legal_mask, recovery_population

    population = generate(SimConfig(), seed=4242, tenant_id=TENANT, cycles=2500)
    failed = recovery_population(population)
    assert failed

    oracle = last_slot = 0
    for cycle in failed:
        legal = legal_mask(cycle.due_at)
        present = funds_present(cycle)
        oracle += bool((legal & present).any())
        slots = np.flatnonzero(legal)
        if slots.size:
            last_slot += bool(present[slots[-1]])

    assert last_slot == oracle, (
        f"maximal patience recovered {last_slot} of an achievable {oracle} — "
        "absorbing funding should leave no gap at all"
    )


def test_under_non_absorbing_funding_maximal_patience_is_not_the_oracle() -> None:
    """The other half: §21's own dynamics leave large headroom, and the
    ordering the absorbing formulation assumes is reversed — firing early beats
    firing late. This is the regime a liquidity model exists for."""
    from prayas.measure.harness import legal_mask, recovery_population

    population = generate(SimConfig(non_absorbing=True), seed=4242, tenant_id=TENANT, cycles=2500)
    failed = recovery_population(population)
    assert failed

    oracle = first_slot = last_slot = 0
    for cycle in failed:
        legal = legal_mask(cycle.due_at)
        present = funds_present(cycle)
        oracle += bool((legal & present).any())
        slots = np.flatnonzero(legal)
        if slots.size:
            first_slot += bool(present[slots[0]])
            last_slot += bool(present[slots[-1]])

    assert last_slot < oracle * 0.75, "non-absorbing funding left no headroom to find"
    assert first_slot > last_slot, (
        "firing at the first legal slot should beat firing at the last once money "
        "can leave — if not, the non-absorbing dynamics are not biting"
    )
