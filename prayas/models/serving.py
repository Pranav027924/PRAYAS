"""Serving V1 through the Phase 8 loop, and falling back to V0 (ADR-071).

Phase 11's exit criterion is that V1 beats V0 "on held-out log-loss **and** on
end-to-end incremental lift — the second is the one that counts". A better
log-loss is a claim about probabilities; a better lift is a claim about money.
They come apart, and when they do the money is what was promised.

So both models are served through *the same loop*: same gate, same budget, same
legal mask, same scoring against ground truth. The only thing that varies is the
hazard curve handed to the sequencer. Anything else varying would make the
comparison a fact about the harness.

**Band hazards become hourly hazards through the survival identity**, not by
division. If a band spanning `k` hours has hazard `h_band`, then surviving the
band means surviving each of its hours:

    1 - h_band = (1 - h_hour)^k    =>    h_hour = 1 - (1 - h_band)^(1/k)

Spreading `h_band / k` instead would be wrong in the direction that matters — it
understates early hours and overstates late ones, which is exactly where a
stopping decision lives.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Final

import numpy as np
import numpy.typing as npt

from prayas.inference.bands import hour_band
from prayas.measure.harness import HORIZON_SLOTS, empirical_hazards
from prayas.models.features import (
    HORIZON_DAYS,
    IST,
    SLOTS_PER_DAY,
    CustomerHistory,
    build_dataset,
    build_history,
    build_presence_dataset,
    hourly_features,
    presence_prior,
    slot_features,
)
from prayas.models.hazard_v1 import HazardV0, HazardV1
from prayas.sim.generate import SimulatedCycle

FloatArray = npt.NDArray[np.float64]

#: Hours per band, in band order — how many hourly slots each band covers.
_BAND_HOURS: Final[tuple[int, ...]] = tuple(
    sum(1 for hour in range(24) if hour_band(float(hour)) == band) for band in range(SLOTS_PER_DAY)
)


def band_to_hourly(band_hazards: FloatArray, *, horizon: int = HORIZON_SLOTS) -> FloatArray:
    """Expand a `(day, band)` curve onto the harness's hourly grid.

    Each hour inside a band carries the per-hour hazard whose `k`-fold survival
    equals the band's — see the module docstring. The result is directly
    comparable with `empirical_hazards`, which is what makes the two arms
    scoreable against each other.
    """
    if band_hazards.ndim != 1 or band_hazards.size != HORIZON_DAYS * SLOTS_PER_DAY:
        raise ValueError(f"expected {HORIZON_DAYS * SLOTS_PER_DAY} band hazards")

    hourly = np.empty(horizon, dtype=np.float64)
    for slot in range(horizon):
        day, hour = divmod(slot, 24)
        band = hour_band(float(hour))
        band_hazard = float(band_hazards[day * SLOTS_PER_DAY + band])
        k = _BAND_HOURS[band]
        hourly[slot] = 1.0 - (1.0 - band_hazard) ** (1.0 / k)
    return np.clip(hourly, 1e-6, 1.0 - 1e-6)


def _histories(train: list[SimulatedCycle]) -> dict[str, CustomerHistory]:
    """One history per customer, from their training cycles.

    Built once. Rebuilding per cycle is the same computation repeated for every
    slot of every cycle, and it dominates the run.
    """
    by_customer: dict[str, list[SimulatedCycle]] = {}
    for cycle in train:
        by_customer.setdefault(cycle.customer_id, []).append(cycle)
    for owned in by_customer.values():
        owned.sort(key=lambda c: c.due_at)
    return {customer: build_history(owned) for customer, owned in by_customer.items()}


_EMPTY = build_history([])


def _feature_matrix(
    cycle: SimulatedCycle, history: CustomerHistory, prior: dict[tuple[int, int], float]
) -> FloatArray:
    return np.asarray(
        [
            slot_features(
                cycle,
                history,
                slot,
                segment_prior_hazard=prior.get((slot // SLOTS_PER_DAY, slot % SLOTS_PER_DAY), 0.02),
            )
            for slot in range(HORIZON_DAYS * SLOTS_PER_DAY)
        ],
        dtype=np.float64,
    )


def _segment_prior(train: list[SimulatedCycle]) -> dict[tuple[int, int], float]:
    """Mean band-level hazard per `(day, band)`, from the training split."""
    hourly = empirical_hazards(train)
    prior: dict[tuple[int, int], float] = {}
    for day in range(HORIZON_DAYS):
        for band in range(SLOTS_PER_DAY):
            hours = [
                day * 24 + hour
                for hour in range(24)
                if hour_band(float(hour)) == band and day * 24 + hour < hourly.size
            ]
            if hours:
                # Survival across the band, converted back to a band hazard.
                survival = float(np.prod(1.0 - hourly[hours]))
                prior[(day, band)] = 1.0 - survival
    return prior


def v1_provider(
    model_factory: Callable[[], HazardV1] | None = None,
) -> Callable[[list[SimulatedCycle]], Callable[[SimulatedCycle], FloatArray]]:
    """A `HazardProvider` serving V1's per-customer curves."""

    def provider(train: list[SimulatedCycle]) -> Callable[[SimulatedCycle], FloatArray]:
        prior = _segment_prior(train)
        dataset = build_dataset(train, segment_prior=prior)
        model = (model_factory() if model_factory else HazardV1()).fit(dataset)
        histories = _histories(train)

        def curve_for(cycle: SimulatedCycle) -> FloatArray:
            history = histories.get(cycle.customer_id, _EMPTY)
            bands = model.predict(_feature_matrix(cycle, history, prior))
            return band_to_hourly(bands)

        return curve_for

    return provider


def v0_provider() -> Callable[[list[SimulatedCycle]], Callable[[SimulatedCycle], FloatArray]]:
    """A `HazardProvider` serving V0's segment lookup, on the same grid.

    Not the same as `population_hazard_provider`: that one is the raw hourly
    empirical curve, while this is V0 as §21 defines it — a segment lookup with
    shrinkage — expressed through the identical band grid V1 uses. Comparing V1
    against this isolates the model; comparing it against the raw curve would
    also be measuring the grid.
    """

    def provider(train: list[SimulatedCycle]) -> Callable[[SimulatedCycle], FloatArray]:
        prior = _segment_prior(train)
        dataset = build_dataset(train, segment_prior=prior)
        model = HazardV0().fit(dataset)
        histories = _histories(train)

        def curve_for(cycle: SimulatedCycle) -> FloatArray:
            history = histories.get(cycle.customer_id, _EMPTY)
            bands = model.predict(_feature_matrix(cycle, history, prior))
            return band_to_hourly(bands)

        return curve_for

    return provider


def _presence_curve(
    model: HazardV0 | HazardV1,
    cycle: SimulatedCycle,
    history: CustomerHistory,
    legal: npt.NDArray[np.bool_],
    prior: dict[tuple[int, int], float],
    default_prior: float = 0.02,
) -> FloatArray:
    """`P(funds present at t)` over the hourly horizon (ADR-075).

    Illegal hours are set to zero rather than predicted. The DP masks them out
    anyway, so a prediction there is wasted work — and a non-zero value in a
    slot nothing can act on is a number waiting to be misread.
    """
    hours = np.flatnonzero(legal)
    curve = np.zeros(HORIZON_SLOTS, dtype=np.float64)
    if hours.size == 0:
        return curve

    x = np.asarray(
        [
            hourly_features(
                cycle,
                history,
                int(h),
                segment_prior_hazard=prior.get(_prior_key(cycle, int(h)), default_prior),
            )
            for h in hours
        ],
        dtype=np.float64,
    )
    curve[hours] = np.clip(model.predict(x), 0.0, 1.0)
    return curve


def _prior_key(cycle: SimulatedCycle, hour: int) -> tuple[int, int]:
    local = (cycle.due_at + timedelta(hours=hour)).astimezone(IST)
    return hour // 24, hour_band(local.hour + local.minute / 60.0)


def presence_provider(
    which: str,
    presence_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
    legal_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
) -> Callable[[list[SimulatedCycle]], Callable[[SimulatedCycle], FloatArray]]:
    """A provider of ADR-075 presence curves, from V0 or V1 on identical rows."""
    if which not in {"v0", "v1"}:
        raise ValueError(f"which must be 'v0' or 'v1', got {which!r}")

    def provider(train: list[SimulatedCycle]) -> Callable[[SimulatedCycle], FloatArray]:
        prior = presence_prior(train, presence_of, legal_of)
        dataset = build_presence_dataset(train, presence_of, legal_of, prior=prior)
        model: HazardV0 | HazardV1 = HazardV0() if which == "v0" else HazardV1()
        model.fit(dataset)
        histories = _histories(train)

        def curve_for(cycle: SimulatedCycle) -> FloatArray:
            history = histories.get(cycle.customer_id, _EMPTY)
            return _presence_curve(model, cycle, history, legal_of(cycle), prior)

        return curve_for

    return provider


def fallback_provider(
    primary: Callable[[list[SimulatedCycle]], Callable[[SimulatedCycle], FloatArray]],
    secondary: Callable[[list[SimulatedCycle]], Callable[[SimulatedCycle], FloatArray]],
    *,
    enabled: Callable[[], bool],
) -> Callable[[list[SimulatedCycle]], Callable[[SimulatedCycle], FloatArray]]:
    """Serve `primary` while `enabled()`, otherwise `secondary`.

    `enabled` is consulted **per cycle**, not once at fit time. That is what
    makes killing V1 mid-run a real test: the fallback has to take effect on the
    next decision, not at the next deploy. Both models are fitted up front so
    the switch costs nothing at the moment it is needed — an incident is the
    worst time to discover the fallback has to train first.
    """

    def provider(train: list[SimulatedCycle]) -> Callable[[SimulatedCycle], FloatArray]:
        primary_curve = primary(train)
        secondary_curve = secondary(train)

        def curve_for(cycle: SimulatedCycle) -> FloatArray:
            return primary_curve(cycle) if enabled() else secondary_curve(cycle)

        return curve_for

    return provider
