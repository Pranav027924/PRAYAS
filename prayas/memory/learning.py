"""Does memory actually help? (Master Spec §26, §29, §37; Phase 12.)

§29 makes a commercial claim — "the hundredth cycle is meaningfully
better-informed than the first" — and Phase 12's first exit criterion turns it
into a measurable one: *profile-informed hazard beats segment-prior-only on
customers with at least three cycles*.

**The profile is rebuilt forward in time, never assembled from the whole
history.** For each customer, cycles are walked in order and the profile as it
stood *before* a cycle is what features that cycle. Building one profile per
customer from everything and then featuring every cycle with it would let a
model read next month's payday out of this month's row — the same leakage
ADR-030 made structural at the database level, arriving through the memory tier
instead.

**Why the cohort restriction is the spec's, not a convenience.** §37: "build
per-customer profiles where at least three cycles exist; shrink hard toward
segment priors below that." A profile with one observation is mostly prior, so
including those customers would dilute the comparison with rows where the two
models are near-identical by construction — and would understate the effect
rather than flatter it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

import numpy as np
import numpy.typing as npt

from prayas.memory.profile import (
    MIN_CYCLES_FOR_PROFILE,
    CustomerPaymentProfile,
    empty_profile,
    record_failure,
    update_payday,
)
from prayas.models.features import (
    FEATURE_NAMES,
    IST,
    Dataset,
    build_history,
    hourly_features,
)
from prayas.sim.generate import SimulatedCycle

#: Columns the profile adds on top of §21's feature table.
PROFILE_FEATURE_NAMES: Final[tuple[str, ...]] = (
    "profile_payday_mass",
    "profile_confidence",
    "profile_days_from_modal_payday",
    "profile_observations",
)

#: The full vector a profile-informed model consumes.
INFORMED_FEATURE_NAMES: Final[tuple[str, ...]] = FEATURE_NAMES + PROFILE_FEATURE_NAMES


def _settled(cycle: SimulatedCycle) -> bool:
    """Whether the cycle cleared. A settled cycle poses the sequencer no
    question, and it is the only kind that moves the payday posterior."""
    return any(e.get("body", {}).get("event") == "payment.captured" for e in cycle.observables)


def profile_features(
    profile: CustomerPaymentProfile, cycle: SimulatedCycle, hour: int
) -> list[float]:
    """What the profile knows about this specific hour.

    `profile_days_from_modal_payday` is signed and wrapped over the month: a
    slot three days *after* payday is a very different prospect from one three
    days before, and an unsigned distance would collapse them.
    """
    local = (cycle.due_at + timedelta(hours=hour)).astimezone(IST)
    at = cycle.due_at

    modal = profile.modal_payday()
    if modal is None:
        distance = -99.0
    else:
        raw = (local.day - modal) % 31
        distance = float(raw if raw <= 15 else raw - 31)

    return [
        profile.payday_probability(local.day, at),
        profile.payday_confidence,
        distance,
        profile.observations,
    ]


@dataclass(frozen=True, slots=True)
class LearningPoint:
    """One rung of the learning curve."""

    cycles_observed: int
    n_rows: int
    log_loss_informed: float
    log_loss_segment_only: float

    @property
    def improvement(self) -> float:
        """Nats saved. Positive means memory helped."""
        return self.log_loss_segment_only - self.log_loss_informed


def build_profile_dataset(
    cycles: list[SimulatedCycle],
    presence_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
    legal_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
    *,
    min_cycles: int = MIN_CYCLES_FOR_PROFILE,
    default_prior: float = 0.02,
    tenant_id: str = "t_sim",
) -> tuple[Dataset, Dataset, npt.NDArray[np.int64]]:
    """Rows with and without profile features, plus each row's cycle index.

    Returns `(segment_only, informed, cycles_observed)`. Both datasets cover the
    *same rows in the same order*, so the two models are compared on identical
    evidence and any difference is the profile's doing. `cycles_observed` is how
    many cycles that customer had already been seen for, which is the x-axis of
    the learning curve.
    """
    by_customer: dict[str, list[SimulatedCycle]] = {}
    for cycle in cycles:
        by_customer.setdefault(cycle.customer_id, []).append(cycle)

    base_rows: list[list[float]] = []
    extra_rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []
    cycle_ids: list[str] = []
    slots: list[int] = []
    observed: list[int] = []

    for customer_id, owned in by_customer.items():
        owned.sort(key=lambda c: c.due_at)
        profile = empty_profile(tenant_id, customer_id)
        seen = 0

        for index, cycle in enumerate(owned):
            failed = not _settled(cycle)

            # Emit rows only once the profile has met §37's bar, and only for
            # failed cycles — a settled cycle poses the sequencer no question.
            if failed and seen >= min_cycles:
                history = build_history(owned[:index])
                present = presence_of(cycle)
                legal = legal_of(cycle)

                for hour in np.flatnonzero(legal).tolist():
                    base = hourly_features(
                        cycle, history, int(hour), segment_prior_hazard=default_prior
                    )
                    base_rows.append(base)
                    extra_rows.append(base + profile_features(profile, cycle, int(hour)))
                    labels.append(int(present[hour]))
                    groups.append(customer_id)
                    cycle_ids.append(cycle.cycle_id)
                    slots.append(int(hour))
                    observed.append(seen)

            # Fold this cycle into the profile *after* featuring, never before.
            if failed:
                profile = record_failure(profile, at=cycle.due_at)
            else:
                local = cycle.due_at.astimezone(IST)
                profile = update_payday(profile, local.day, at=cycle.due_at)
                seen += 1

    if not base_rows:
        raise ValueError(
            "no rows: no customer reached the minimum cycle count with a failure to score"
        )

    def _dataset(rows: list[list[float]]) -> Dataset:
        return Dataset(
            x=np.asarray(rows, dtype=np.float64),
            y=np.asarray(labels, dtype=np.int64),
            groups=np.asarray(groups),
            cycle_ids=np.asarray(cycle_ids),
            slots=np.asarray(slots, dtype=np.int64),
        )

    return _dataset(base_rows), _dataset(extra_rows), np.asarray(observed, dtype=np.int64)
