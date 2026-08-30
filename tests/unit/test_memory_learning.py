"""Profile-informed features and their leakage discipline (§26, §29, §37).

The measurement these features support is only worth anything if the profile
that features a cycle was built **strictly before** that cycle. ADR-030 made
that structural at the database level by withholding a grant; here it has to be
structural in the walk order, so it is asserted directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import numpy.typing as npt
import pytest

from prayas.measure.harness import funds_present, legal_mask
from prayas.memory.learning import (
    INFORMED_FEATURE_NAMES,
    PROFILE_FEATURE_NAMES,
    build_profile_dataset,
    profile_features,
)
from prayas.memory.profile import empty_profile, update_payday
from prayas.models.features import FEATURE_NAMES
from prayas.sim.config import SimConfig
from prayas.sim.generate import SimulatedCycle, generate

T0 = datetime(2026, 3, 1, tzinfo=UTC)
TENANT = "t_sim"


def _legal(cycle: SimulatedCycle) -> npt.NDArray[np.bool_]:
    return legal_mask(cycle.due_at)


def _population(cycles: int = 3000, seed: int = 11) -> list[SimulatedCycle]:
    return generate(
        SimConfig(non_absorbing=True, cycles_per_customer=12),
        seed=seed,
        tenant_id=TENANT,
        cycles=cycles,
    )


# ── the feature vector ─────────────────────────────────────────────────────


def test_the_informed_vector_extends_the_base_one() -> None:
    """Both models must be comparable on identical evidence, so the informed
    vector is the base vector plus profile columns — never a different one."""
    assert INFORMED_FEATURE_NAMES[: len(FEATURE_NAMES)] == FEATURE_NAMES
    assert INFORMED_FEATURE_NAMES[len(FEATURE_NAMES) :] == PROFILE_FEATURE_NAMES


def test_a_cold_profile_reports_a_distinguishable_absence() -> None:
    """ "No payday known" must not collide with "payday is today" — the
    distance sentinel has to sit outside the real range."""
    cycle = _population(200)[0]
    cold = profile_features(empty_profile(TENANT, "c"), cycle, 0)
    assert cold[PROFILE_FEATURE_NAMES.index("profile_days_from_modal_payday")] == -99.0
    assert cold[PROFILE_FEATURE_NAMES.index("profile_observations")] == 0.0


def test_distance_from_payday_is_signed() -> None:
    """Three days after payday is a different prospect from three days before,
    and an unsigned distance would collapse them."""
    cycle = _population(200)[0]
    profile = empty_profile(TENANT, "c")
    for month in range(6):
        profile = update_payday(profile, 15, at=T0 + timedelta(days=30 * month))

    index = PROFILE_FEATURE_NAMES.index("profile_days_from_modal_payday")
    seen = {profile_features(profile, cycle, hour)[index] for hour in range(0, 24 * 20, 24)}
    assert any(d < 0 for d in seen) and any(d > 0 for d in seen)
    assert all(-16 <= d <= 16 for d in seen), "distance must wrap within the month"


# ── leakage ────────────────────────────────────────────────────────────────


def test_the_profile_never_sees_the_cycle_it_features() -> None:
    """The load-bearing property. If a cycle's own outcome reached its features,
    every number downstream would be measuring a leak."""
    population = _population()
    _, informed, observed = build_profile_dataset(population, funds_present, _legal)

    # `profile_observations` is the decayed evidence count. For the first
    # scored cycle of any customer it must equal the count from *earlier*
    # cycles only, which is strictly below the customer's total.
    column = len(FEATURE_NAMES) + PROFILE_FEATURE_NAMES.index("profile_observations")
    per_customer: dict[str, float] = {}
    for group, value in zip(informed.groups.tolist(), informed.x[:, column].tolist(), strict=True):
        per_customer[str(group)] = max(per_customer.get(str(group), 0.0), value)

    ceiling = 1.0 / (1.0 - 0.97)
    assert per_customer, "no customers scored"
    assert all(v < ceiling for v in per_customer.values())
    assert observed.min() >= 3, "§37's three-cycle bar was not applied"


def test_evidence_is_monotone_within_a_customer() -> None:
    """Walking forward in time means a customer's evidence count never falls."""
    population = _population()
    _, informed, _ = build_profile_dataset(population, funds_present, _legal)
    column = len(FEATURE_NAMES) + PROFILE_FEATURE_NAMES.index("profile_observations")

    # Ordered by `due_at`, not by cycle id: the simulator randomises due dates
    # within the month, so id order is not time order — and time order is what
    # the walk actually follows.
    due_at = {c.cycle_id: c.due_at for c in population}

    by_customer: dict[str, dict[str, float]] = {}
    for group, cycle_id, value in zip(
        informed.groups.tolist(),
        informed.cycle_ids.tolist(),
        informed.x[:, column].tolist(),
        strict=True,
    ):
        by_customer.setdefault(str(group), {})[str(cycle_id)] = value

    checked = 0
    for per_cycle in by_customer.values():
        ordered = [v for _, v in sorted(per_cycle.items(), key=lambda kv: due_at[kv[0]])]
        assert ordered == sorted(ordered), "evidence fell as time advanced"
        checked += 1
    assert checked > 10, "too few customers to have tested anything"


def test_both_datasets_cover_the_same_rows_in_the_same_order() -> None:
    """Any difference between the two models must be the profile's doing, so
    they have to be scored on identical evidence."""
    population = _population()
    base, informed, observed = build_profile_dataset(population, funds_present, _legal)

    assert len(base) == len(informed) == len(observed)
    assert np.array_equal(base.y, informed.y)
    assert np.array_equal(base.groups, informed.groups)
    assert np.array_equal(base.slots, informed.slots)
    assert np.array_equal(base.x, informed.x[:, : len(FEATURE_NAMES)])


def test_only_legal_slots_are_scored() -> None:
    population = _population()
    _, informed, _ = build_profile_dataset(population, funds_present, _legal)
    by_id = {c.cycle_id: c for c in population}
    for cycle_id, slot in zip(informed.cycle_ids.tolist(), informed.slots.tolist(), strict=True):
        assert legal_mask(by_id[str(cycle_id)].due_at)[int(slot)]


def test_a_population_with_no_history_raises_rather_than_returning_nothing() -> None:
    """An empty comparison is not a passing comparison."""
    thin = generate(
        SimConfig(non_absorbing=True, cycles_per_customer=1),
        seed=5,
        tenant_id=TENANT,
        cycles=200,
    )
    with pytest.raises(ValueError, match="no rows"):
        build_profile_dataset(thin, funds_present, _legal)
