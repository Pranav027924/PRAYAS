"""The Customer Payment Profile and its update rules (Master Spec §26, §27).

§29's claim is that "the profile tier compounds" — the hundredth cycle is
better-informed than the first. These tests are what make that a measurable
statement rather than a slogan: the posterior has to actually sharpen, the
decays have to actually decay, and a stated hint has to actually expire.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prayas.memory.profile import (
    DAYS_IN_MONTH,
    DECLARED_TTL_DAYS,
    FATIGUE_HALF_LIFE_DAYS,
    MIN_CYCLES_FOR_PROFILE,
    CustomerPaymentProfile,
    ProfileError,
    empty_profile,
    record_contact,
    record_declared_hint,
    record_failure,
    record_revocation,
    record_settlement_lag,
    update_payday,
    withdraw_consent,
)

T0 = datetime(2026, 3, 1, tzinfo=UTC)
TENANT, CUSTOMER = "t_alpha", "cust_1"


def _fresh() -> CustomerPaymentProfile:
    return empty_profile(TENANT, CUSTOMER)


# ── tenant scoping (§27, Invariant 8) ──────────────────────────────────────


def test_a_profile_cannot_exist_without_a_tenant() -> None:
    """§27: "individual memory is strictly tenant-scoped". A profile with no
    tenant is not scoped to anything."""
    with pytest.raises(ProfileError, match="tenant-scoped"):
        CustomerPaymentProfile(tenant_id="", customer_id=CUSTOMER)


def test_a_profile_requires_a_customer() -> None:
    with pytest.raises(ProfileError, match="customer_id"):
        CustomerPaymentProfile(tenant_id=TENANT, customer_id="")


# ── the payday posterior ───────────────────────────────────────────────────


def test_a_cold_profile_knows_nothing_and_says_so() -> None:
    profile = _fresh()
    assert profile.modal_payday() is None
    assert profile.observations == 0.0
    assert profile.payday_confidence == 0.0
    assert not profile.is_informative
    assert profile.payday_probability(15, T0) == 0.0


def test_the_posterior_learns_a_payday() -> None:
    profile = _fresh()
    for month in range(6):
        profile = update_payday(profile, 1, at=T0 + timedelta(days=30 * month))

    assert profile.modal_payday() == 1
    assert profile.payday_probability(1, T0) > 0.9
    assert profile.tenure_cycles == 6
    assert profile.is_informative


def test_the_posterior_sharpens_with_observations() -> None:
    """§29's compounding, as an assertion. If confidence does not rise with
    evidence, the profile tier is not an accumulating asset."""
    profile = _fresh()
    confidences = []
    for month in range(8):
        profile = update_payday(profile, 7, at=T0 + timedelta(days=30 * month))
        confidences.append(profile.payday_confidence)

    assert confidences == sorted(confidences), "confidence must not fall as evidence arrives"
    assert confidences[-1] > confidences[0]


def test_the_posterior_reads_as_a_distribution() -> None:
    """Stored as unnormalised evidence (ADR-077), read as probabilities."""
    profile = _fresh()
    for day in (1, 1, 7, 15, 1):
        profile = update_payday(profile, day, at=T0)

    read = profile.normalised_posterior()
    assert sum(read.values()) == pytest.approx(1.0)
    assert all(0.0 <= m <= 1.0 for m in read.values())
    assert profile.observations > 1.0, "raw counts must accumulate, not normalise away"


def test_older_observations_lose_influence() -> None:
    """§26's decay. A payday that moved must stop outvoting the one in use."""
    profile = _fresh()
    for _ in range(6):
        profile = update_payday(profile, 1, at=T0)
    assert profile.modal_payday() == 1

    for _ in range(12):
        profile = update_payday(profile, 20, at=T0)
    assert profile.modal_payday() == 20, "the posterior never followed the move"


def test_only_successful_debits_move_the_payday_posterior() -> None:
    """A failure says the account was empty then — not when money arrives.
    Folding failures in would teach the posterior the retry schedule."""
    profile = update_payday(_fresh(), 5, at=T0)
    before = dict(profile.payday_posterior)

    failed = record_failure(profile, at=T0 + timedelta(days=1))
    assert failed.payday_posterior == before
    assert failed.consecutive_failures == 1
    assert failed.tenure_cycles == profile.tenure_cycles


def test_a_success_clears_the_failure_streak() -> None:
    profile = record_failure(record_failure(_fresh(), at=T0), at=T0)
    assert profile.consecutive_failures == 2
    assert update_payday(profile, 3, at=T0).consecutive_failures == 0


@pytest.mark.parametrize("day", [0, 32, -1])
def test_impossible_days_are_refused(day: int) -> None:
    with pytest.raises(ProfileError, match="out of range"):
        update_payday(_fresh(), day, at=T0)


def test_the_thirty_first_is_a_real_payday() -> None:
    """Dropping it would silently put its mass on the wrong day."""
    assert update_payday(_fresh(), DAYS_IN_MONTH, at=T0).modal_payday() == DAYS_IN_MONTH


# ── declared hints (§25, §26, Invariant 9) ─────────────────────────────────


def test_a_declared_hint_shifts_a_thin_posterior() -> None:
    """Invariant 9: LLM output "never becomes an instruction" but may shift a
    prior. With almost no evidence, a fresh hint should dominate."""
    profile = record_declared_hint(_fresh(), 5, at=T0)
    assert profile.payday_probability(5, T0) > 0.9


def test_a_declared_hint_expires_completely() -> None:
    """§26 — "decays to zero over 180 days". An exponential never reaches zero;
    a hint meant to expire has to actually expire."""
    profile = record_declared_hint(_fresh(), 5, at=T0)
    assert profile.declared_weight(T0) == pytest.approx(1.0)
    assert profile.declared_weight(T0 + timedelta(days=90)) == pytest.approx(0.5, abs=0.01)
    assert profile.declared_weight(T0 + timedelta(days=DECLARED_TTL_DAYS)) == 0.0
    assert profile.declared_weight(T0 + timedelta(days=365)) == 0.0


def test_evidence_overrules_a_stale_hint() -> None:
    """The hint recedes as observations accumulate — it is a prior, not a rule."""
    profile = record_declared_hint(_fresh(), 5, at=T0)
    for month in range(10):
        profile = update_payday(profile, 20, at=T0 + timedelta(days=30 * month))

    later = T0 + timedelta(days=300)
    assert profile.payday_probability(20, later) > profile.payday_probability(5, later)


def test_a_hint_from_the_future_is_refused() -> None:
    profile = record_declared_hint(_fresh(), 5, at=T0 + timedelta(days=10))
    with pytest.raises(ProfileError, match="future"):
        profile.declared_weight(T0)


def test_payday_probability_is_a_distribution_over_days() -> None:
    """Blending a hint with evidence must not create or destroy mass."""
    profile = record_declared_hint(update_payday(_fresh(), 3, at=T0), 5, at=T0)
    total = sum(profile.payday_probability(d, T0) for d in range(1, DAYS_IN_MONTH + 1))
    assert total == pytest.approx(1.0)


# ── fatigue (§26, §24.6) ───────────────────────────────────────────────────


def test_fatigue_halves_over_the_half_life() -> None:
    profile = record_contact(_fresh(), at=T0)
    assert profile.current_fatigue(T0) == pytest.approx(1.0)
    assert profile.current_fatigue(T0 + timedelta(days=FATIGUE_HALF_LIFE_DAYS)) == pytest.approx(
        0.5, abs=1e-9
    )


def test_fatigue_is_path_dependent() -> None:
    """Three messages in a week must land harder than three over three months —
    §24.6 needs this to be able to choose silence."""
    burst = _fresh()
    for day in (0, 1, 2):
        burst = record_contact(burst, at=T0 + timedelta(days=day))

    spread = _fresh()
    for day in (0, 45, 90):
        spread = record_contact(spread, at=T0 + timedelta(days=day))

    at = T0 + timedelta(days=90)
    assert burst.current_fatigue(T0 + timedelta(days=2)) > spread.current_fatigue(at)


def test_reading_fatigue_raw_would_overstate_a_stale_profile() -> None:
    profile = record_contact(_fresh(), at=T0)
    stale = T0 + timedelta(days=120)
    assert profile.fatigue_score == pytest.approx(1.0)
    assert profile.current_fatigue(stale) < 0.01


def test_an_uncontacted_customer_has_no_fatigue() -> None:
    assert _fresh().current_fatigue(T0) == 0.0


# ── risk and governance ────────────────────────────────────────────────────


def test_revocations_and_lags_accumulate() -> None:
    profile = record_revocation(_fresh(), at=T0)
    profile = record_settlement_lag(profile, 36.5, at=T0)
    assert profile.revocation_events == 1
    assert profile.fail_success_lag_days == (36.5,)


def test_settlement_lags_are_bounded() -> None:
    profile = _fresh()
    for i in range(40):
        profile = record_settlement_lag(profile, float(i), at=T0, keep=24)
    assert len(profile.fail_success_lag_days) == 24
    assert profile.fail_success_lag_days[-1] == 39.0


def test_withdrawing_consent_flags_but_does_not_erase() -> None:
    """The flag and the cascade are different operations. A flag set without
    the cascade having run is a profile still holding data it should not, and
    conflating them would hide exactly that state."""
    profile = update_payday(_fresh(), 5, at=T0)
    withdrawn = withdraw_consent(profile, at=T0)

    assert withdrawn.consent_withdrawn
    assert withdrawn.payday_posterior, "erasure is prayas.memory.forget's job, not this one's"


def test_money_stays_integer_paise() -> None:
    with pytest.raises(ProfileError, match="integer paise"):
        CustomerPaymentProfile(
            tenant_id=TENANT,
            customer_id=CUSTOMER,
            typical_amount_p75=1999.5,  # type: ignore[arg-type]
        )


def test_the_three_cycle_bar_is_enforced() -> None:
    """§37: "build per-customer profiles where at least three cycles exist"."""
    profile = _fresh()
    for i in range(MIN_CYCLES_FOR_PROFILE):
        assert not profile.is_informative
        profile = update_payday(profile, 5, at=T0 + timedelta(days=30 * i))
    assert profile.is_informative


def test_updates_do_not_mutate_the_original() -> None:
    """Frozen on purpose: a half-applied update would leave the posterior
    normalised against a decay that was never applied."""
    profile = update_payday(_fresh(), 5, at=T0)
    snapshot = dict(profile.payday_posterior)
    update_payday(profile, 20, at=T0)
    assert profile.payday_posterior == snapshot


def test_one_observation_is_not_certainty() -> None:
    """Concentration is not confidence. A single cycle is perfectly
    concentrated and says almost nothing; reporting 1.0 would hand the
    sequencer a certainty one cycle cannot buy."""
    one = update_payday(_fresh(), 5, at=T0)
    assert one.payday_confidence < 0.25

    many = one
    for month in range(1, 15):
        many = update_payday(many, 5, at=T0 + timedelta(days=30 * month))
    assert many.payday_confidence > 0.6


def test_a_scattered_posterior_is_not_confident() -> None:
    """Plenty of evidence, no pattern — the honest answer is low confidence."""
    profile = _fresh()
    for i, day in enumerate(range(1, 25)):
        profile = update_payday(profile, day, at=T0 + timedelta(days=30 * i))
    assert profile.payday_confidence < 0.35


def test_the_posterior_now_resists_a_single_outlier() -> None:
    """ADR-077's payoff, and the difference §29 depends on.

    Under §26's literal in-place normalisation one stray observation claimed
    ~51% of the mass however long the history. With counts kept raw, a settled
    payday survives an outlier and a *sustained* change still wins.
    """
    settled = _fresh()
    for month in range(20):
        settled = update_payday(settled, 5, at=T0 + timedelta(days=30 * month))

    outlier = update_payday(settled, 20, at=T0 + timedelta(days=630))
    assert outlier.modal_payday() == 5, "one stray cycle overturned twenty"
    assert outlier.normalised_posterior()[20] < 0.15

    moved = outlier
    for month in range(21, 40):
        moved = update_payday(moved, 20, at=T0 + timedelta(days=30 * month))
    assert moved.modal_payday() == 20, "a sustained move must still be learned"


def test_evidence_saturates_rather_than_growing_without_bound() -> None:
    """A customer observed for ten years is not thirty times more knowable than
    one observed for one — the decay has already discarded the old evidence."""
    profile = _fresh()
    for month in range(200):
        profile = update_payday(profile, 5, at=T0 + timedelta(days=30 * month))

    ceiling = 1.0 / (1.0 - 0.97)
    assert profile.observations == pytest.approx(ceiling, rel=0.02)
    assert profile.payday_confidence < 1.0
