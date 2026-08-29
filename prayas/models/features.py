"""Discrete-time hazard features (Master Spec §21; ADR-030, ADR-067).

§21 asks: "for this customer, what is `P(account funded >= A)` as a function of
time?" — and answers it with a *discrete-time hazard*, one row per
(cycle, slot) at risk:

    h(t) = P(funded >= A at slot t | not funded before t)

**Censoring is structural, not a correction applied afterwards.** A cycle emits
rows only for the slots it actually reached: rows stop at the funding slot, and
a cycle that never funds inside the horizon emits every slot with label 0 and is
right-censored there. That is what §21 means by "an hour never attempted is an
outcome never observed" — modelled by which rows exist, so no later step can
forget it.

**Slots are `(day_offset, hour_band)`**, the same grid V0's `segment_priors` are
keyed on. Sharing the grid is what lets Phase 11's exit criterion — "V1 beats V0
on held-out log-loss" — compare two answers to one question rather than two
questions.

**No leakage, by construction (ADR-030).** Features for a cycle are built only
from that customer's *strictly earlier* cycles, and only from observables. The
ground-truth block supplies the label and nothing else; nothing here reads
`issuer_state`, `payday_archetype`, or `true_cause`. Phase 4 made this
structural at the database level by withholding the SELECT grant on truth
columns; this module is the in-process equivalent, and the guard is that
`build_dataset` takes the truth it needs for labels through one narrow path.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Final

import numpy as np
import numpy.typing as npt

from prayas.inference.bands import HOUR_BANDS, hour_band, ticket_band
from prayas.sim.generate import SimulatedCycle

IST: Final = timezone(timedelta(hours=5, minutes=30))

#: Slots per day — one per hour band (§36's `segment_priors` grid).
SLOTS_PER_DAY: Final = len(HOUR_BANDS)

#: §21 — "a 30-day horizon cap", one of the stated mitigations for funding not
#: being strictly absorbing.
HORIZON_DAYS: Final = 30

#: Total slots in the horizon.
HORIZON_SLOTS: Final = HORIZON_DAYS * SLOTS_PER_DAY

#: Representative IST hour for each band — the midpoint of its window, so a
#: slot's timestamp sits inside the window it names rather than on its edge.
_BAND_MIDPOINT: Final[dict[int, float]] = {0: 5.0, 1: 11.5, 2: 15.0, 3: 19.25, 4: 22.75}

#: Column order. Fixed and exported because the registry pins it: a model
#: reloaded against a different column order is silently wrong, not broken.
FEATURE_NAMES: Final[tuple[str, ...]] = (
    "dom_hist_density",
    "days_since_payday",
    "amount_ratio",
    "hour_success_rate",
    "n_concurrent_mandates",
    "prior_lag_median",
    "segment_prior_hazard",
    "day_offset",
    "day_of_month",
    "hour_band",
    "ticket_band",
    "rail_code",
    "n_prior_cycles",
)

_RAIL_CODE: Final[dict[str, int]] = {"upi_autopay": 0, "card_emandate": 1, "enach": 2}


def slot_time(due_at: datetime, slot: int) -> datetime:
    """The UTC instant representing `slot`, counted from the cycle's due time.

    Times are stored UTC and evaluated IST (project standard), so the band's
    midpoint hour is applied on the IST calendar day and converted back.
    """
    if not 0 <= slot < HORIZON_SLOTS:
        raise ValueError(f"slot must be in [0, {HORIZON_SLOTS}), got {slot}")

    day_offset, band = divmod(slot, SLOTS_PER_DAY)
    day = (due_at.astimezone(IST) + timedelta(days=day_offset)).date()
    hour = _BAND_MIDPOINT[band]
    return datetime(
        day.year,
        day.month,
        day.day,
        int(hour),
        round((hour % 1) * 60),
        tzinfo=IST,
    ).astimezone(UTC)


def _event_times(cycle: SimulatedCycle, event: str) -> list[datetime]:
    return [
        datetime.fromtimestamp(int(body["created_at"]), tz=UTC)
        for e in cycle.observables
        if (body := e.get("body", {})).get("event") == event and "created_at" in body
    ]


@dataclass(frozen=True, slots=True)
class CustomerHistory:
    """What is knowable about a customer from their earlier cycles alone.

    Every field here is §21's feature table, computed from observables. Empty
    history is the cold-start case and yields the neutral values the segment
    prior is meant to carry — §21's `w = n/(n+kappa)` shrinkage is applied by
    the model consuming these, not smuggled in here.
    """

    #: Day-of-month density of historical *successful* debits (§21).
    success_days_of_month: tuple[int, ...]
    #: Success count by hour band, and attempts by hour band.
    band_successes: tuple[int, ...]
    band_attempts: tuple[int, ...]
    #: Amounts of prior successful debits, integer paise. Money is never float.
    success_amounts_paise: tuple[int, ...]
    #: Median fail -> success lag in hours, prior cycles.
    prior_lag_median_hours: float | None
    #: Distinct mandates this customer holds — competition for the same rupees.
    n_concurrent_mandates: int
    #: How much history there is, which is what cold-start shrinkage keys on.
    n_prior_cycles: int

    @property
    def p75_success_paise(self) -> int | None:
        """p75 of prior successful debits — §21's `amount_ratio` denominator."""
        if not self.success_amounts_paise:
            return None
        return int(np.percentile(np.asarray(self.success_amounts_paise, dtype=np.int64), 75))

    def dom_density(self, day_of_month: int) -> float:
        """Share of prior successes that landed on this day of month.

        The single most informative thing about an Indian salary account, and
        the reason §21 says a lookup "already beats a calendar".
        """
        if not self.success_days_of_month:
            return 0.0
        hits = sum(1 for d in self.success_days_of_month if d == day_of_month)
        return hits / len(self.success_days_of_month)

    def inferred_payday(self) -> int | None:
        """Modal day-of-month of prior successes — the funding cycle §21 infers."""
        if not self.success_days_of_month:
            return None
        counts = np.bincount(np.asarray(self.success_days_of_month, dtype=np.int64), minlength=32)
        return int(np.argmax(counts))

    def days_since_payday(self, day_of_month: int) -> float:
        """Days from the inferred payday to this slot, wrapped over the month.

        Returns -1.0 when no payday can be inferred — a distinct value rather
        than 0, because "no history" and "today is payday" must not collide.
        """
        payday = self.inferred_payday()
        if payday is None:
            return -1.0
        return float((day_of_month - payday) % 30)

    def band_success_rate(self, band: int) -> float:
        """Historical success by hour-of-day (§21), 0.5 where unobserved."""
        attempts = self.band_attempts[band]
        if attempts == 0:
            return 0.5
        return self.band_successes[band] / attempts


_EMPTY_HISTORY: Final = CustomerHistory(
    success_days_of_month=(),
    band_successes=(0,) * SLOTS_PER_DAY,
    band_attempts=(0,) * SLOTS_PER_DAY,
    success_amounts_paise=(),
    prior_lag_median_hours=None,
    n_concurrent_mandates=1,
    n_prior_cycles=0,
)


def build_history(prior: list[SimulatedCycle]) -> CustomerHistory:
    """Fold a customer's earlier cycles into §21's features.

    `prior` must contain only cycles strictly before the one being featurised.
    Enforcing that is `iter_cycle_features`' job; this function trusts it, which
    is why nothing else should call it with an unfiltered list.
    """
    if not prior:
        return _EMPTY_HISTORY

    days: list[int] = []
    amounts: list[int] = []
    successes = [0] * SLOTS_PER_DAY
    attempts = [0] * SLOTS_PER_DAY
    lags: list[float] = []

    for cycle in prior:
        captured = _event_times(cycle, "payment.captured")
        failed = _event_times(cycle, "payment.failed")

        for at in captured:
            local = at.astimezone(IST)
            days.append(local.day)
            amounts.append(cycle.amount_paise)
            band = hour_band(local.hour + local.minute / 60.0)
            successes[band] += 1
            attempts[band] += 1

        for at in failed:
            local = at.astimezone(IST)
            attempts[hour_band(local.hour + local.minute / 60.0)] += 1

        if captured and failed:
            lags.append((min(captured) - min(failed)).total_seconds() / 3600.0)

    return CustomerHistory(
        success_days_of_month=tuple(days),
        band_successes=tuple(successes),
        band_attempts=tuple(attempts),
        success_amounts_paise=tuple(amounts),
        prior_lag_median_hours=float(np.median(lags)) if lags else None,
        n_concurrent_mandates=len({c.mandate_id for c in prior}),
        n_prior_cycles=len(prior),
    )


def slot_features(
    cycle: SimulatedCycle,
    history: CustomerHistory,
    slot: int,
    *,
    segment_prior_hazard: float,
) -> list[float]:
    """One row of `FEATURE_NAMES`, in that order.

    `segment_prior_hazard` is V0's own estimate for this slot. Feeding it in
    means V1 *nests* V0 rather than competing from scratch: if the trained model
    finds nothing better, it can pass the lookup through, so beating V0 on
    held-out log-loss is a statement about added information rather than about
    who got the luckier features.
    """
    day_offset, band = divmod(slot, SLOTS_PER_DAY)
    local = slot_time(cycle.due_at, slot).astimezone(IST)

    p75 = history.p75_success_paise
    amount_ratio = cycle.amount_paise / p75 if p75 else 1.0

    return [
        history.dom_density(local.day),
        history.days_since_payday(local.day),
        amount_ratio,
        history.band_success_rate(band),
        float(history.n_concurrent_mandates),
        history.prior_lag_median_hours if history.prior_lag_median_hours is not None else -1.0,
        segment_prior_hazard,
        float(day_offset),
        float(local.day),
        float(band),
        float(ticket_band(cycle.amount_paise)),
        float(_RAIL_CODE.get(cycle.rail, -1)),
        float(history.n_prior_cycles),
    ]


#: Hourly slots the sequencer actually reasons over (§38's 30-day cap).
HOURLY_HORIZON: Final = HORIZON_DAYS * 24


def hourly_features(
    cycle: SimulatedCycle,
    history: CustomerHistory,
    hour: int,
    *,
    segment_prior_hazard: float,
) -> list[float]:
    """`FEATURE_NAMES` for an *hourly* slot (ADR-075's presence model).

    The same columns as `slot_features`, computed at hour resolution rather
    than band resolution. Presence is what the DP now consumes, and it is asked
    about a specific hour — so the features have to be about that hour, not
    about the band containing it. Sharing the column list keeps the registry's
    pinned `feature_names` meaningful across both models.
    """
    if not 0 <= hour < HOURLY_HORIZON:
        raise ValueError(f"hour must be in [0, {HOURLY_HORIZON}), got {hour}")

    local = (cycle.due_at + timedelta(hours=hour)).astimezone(IST)
    band = hour_band(local.hour + local.minute / 60.0)

    p75 = history.p75_success_paise
    amount_ratio = cycle.amount_paise / p75 if p75 else 1.0

    return [
        history.dom_density(local.day),
        history.days_since_payday(local.day),
        amount_ratio,
        history.band_success_rate(band),
        float(history.n_concurrent_mandates),
        history.prior_lag_median_hours if history.prior_lag_median_hours is not None else -1.0,
        segment_prior_hazard,
        float(hour // 24),
        float(local.day),
        float(band),
        float(ticket_band(cycle.amount_paise)),
        float(_RAIL_CODE.get(cycle.rail, -1)),
        float(history.n_prior_cycles),
    ]


def funding_slot(cycle: SimulatedCycle) -> int | None:
    """The slot in which funds arrived, or None if never inside the horizon.

    This is the only place ground truth is read, and it is read as a *label*.
    §38: "Ground truth is the entire point: it is what allows cause inference to
    be validated with a real confusion matrix." Nothing derived here reaches
    `slot_features`.
    """
    funded_at = cycle.truth.true_funding_time
    if funded_at is None:
        return None

    delta = funded_at - cycle.due_at
    if delta.total_seconds() < 0:
        return 0

    local = funded_at.astimezone(IST)
    day_offset = (local.date() - cycle.due_at.astimezone(IST).date()).days
    if not 0 <= day_offset < HORIZON_DAYS:
        return None

    return day_offset * SLOTS_PER_DAY + hour_band(local.hour + local.minute / 60.0)


@dataclass(frozen=True, slots=True)
class Dataset:
    """Discrete-time hazard rows, with the grouping a fair split needs."""

    x: npt.NDArray[np.float64]
    y: npt.NDArray[np.int64]
    #: Customer id per row. Splitting on this rather than at random is what
    #: stops the same customer's cycles appearing on both sides of the split,
    #: which would let a model memorise a payday and call it generalisation.
    groups: npt.NDArray[np.str_]
    #: Cycle id per row, so a curve can be reassembled from predictions.
    cycle_ids: npt.NDArray[np.str_]
    #: Slot index per row.
    slots: npt.NDArray[np.int64]

    def __len__(self) -> int:
        return int(self.x.shape[0])


def _cycle_failed(cycle: SimulatedCycle) -> bool:
    return any(e.get("body", {}).get("event") == "payment.failed" for e in cycle.observables)


def build_dataset(
    cycles: list[SimulatedCycle],
    *,
    segment_prior: dict[tuple[int, int], float] | None = None,
    default_prior: float = 0.02,
    max_slots: int = HORIZON_SLOTS,
) -> Dataset:
    """Every (failed cycle, slot-at-risk) row across a population.

    Only *failed* cycles contribute. A cycle that settled on the first attempt
    poses no question — the sequencer is asked when to retry, and it is never
    asked about a cycle that did not fail. Phase 8 learned this the hard way:
    scoring a recovery policy over cycles that never failed credits it with free
    wins and makes every arm look alike.
    """
    if max_slots <= 0 or max_slots > HORIZON_SLOTS:
        raise ValueError(f"max_slots must be in [1, {HORIZON_SLOTS}], got {max_slots}")

    by_customer: dict[str, list[SimulatedCycle]] = {}
    for cycle in cycles:
        by_customer.setdefault(cycle.customer_id, []).append(cycle)

    rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []
    cycle_ids: list[str] = []
    slots: list[int] = []

    for customer_id, owned in by_customer.items():
        owned.sort(key=lambda c: c.due_at)
        for index, cycle in enumerate(owned):
            if not _cycle_failed(cycle):
                continue

            history = build_history(owned[:index])
            event_slot = funding_slot(cycle)
            # Rows exist only for slots the cycle was still at risk in: up to
            # and including the funding slot, or the whole horizon if censored.
            last = event_slot if event_slot is not None else max_slots - 1
            last = min(last, max_slots - 1)

            for slot in range(last + 1):
                _, band = divmod(slot, SLOTS_PER_DAY)
                prior = (segment_prior or {}).get((slot // SLOTS_PER_DAY, band), default_prior)
                rows.append(slot_features(cycle, history, slot, segment_prior_hazard=prior))
                labels.append(1 if slot == event_slot else 0)
                groups.append(customer_id)
                cycle_ids.append(cycle.cycle_id)
                slots.append(slot)

    if not rows:
        raise ValueError("no failed cycles in population — nothing to train on")

    return Dataset(
        x=np.asarray(rows, dtype=np.float64),
        y=np.asarray(labels, dtype=np.int64),
        groups=np.asarray(groups),
        cycle_ids=np.asarray(cycle_ids),
        slots=np.asarray(slots, dtype=np.int64),
    )


def presence_prior(
    cycles: list[SimulatedCycle],
    presence_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
    legal_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
) -> dict[tuple[int, int], float]:
    """Empirical `P(funds present)` per `(day_offset, hour_band)` (ADR-076).

    The presence analogue of V0's segment lookup, and the term that lets V1
    *nest* V0 rather than compete with it from scratch. Without it the model
    has to rediscover the population's shape from per-customer features alone,
    and a constant in that column is a wasted feature — which is exactly what
    the first cut of this path shipped.
    """
    hits: dict[tuple[int, int], list[int]] = {}
    for cycle in cycles:
        if not _cycle_failed(cycle):
            continue
        present = presence_of(cycle)
        for hour in np.flatnonzero(legal_of(cycle)).tolist():
            local = (cycle.due_at + timedelta(hours=int(hour))).astimezone(IST)
            key = (int(hour) // 24, hour_band(local.hour + local.minute / 60.0))
            hits.setdefault(key, []).append(int(present[hour]))

    return {key: float(np.mean(values)) for key, values in hits.items() if values}


def build_presence_dataset(
    cycles: list[SimulatedCycle],
    presence_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
    legal_of: Callable[[SimulatedCycle], npt.NDArray[np.bool_]],
    *,
    prior: dict[tuple[int, int], float] | None = None,
    default_prior: float = 0.02,
) -> Dataset:
    """Rows for ADR-075's presence model: `P(funds present at this hour)`.

    **Legal slots only, and that is not an optimisation.** An hour the gate
    would deny is an hour no attempt can ever be made in, so it is an hour whose
    presence can never be observed and never acted on. Training on it would fit
    a quantity the system cannot use, and it is also what keeps the row count
    tractable — roughly a hundred legal hours per cycle rather than seven
    hundred and twenty.

    **Stated limitation.** In production the label comes from attempts: you
    learn the account was empty at the hours you tried. That is a heavily
    censored, policy-dependent sample, and a model trained on it inherits the
    policy that produced it. Here the simulator supplies presence directly,
    which is the affordance §38 exists to give — but it means these numbers are
    an upper bound on what the same model would learn from real logs.
    """
    by_customer: dict[str, list[SimulatedCycle]] = {}
    for cycle in cycles:
        by_customer.setdefault(cycle.customer_id, []).append(cycle)

    rows: list[list[float]] = []
    labels: list[int] = []
    groups: list[str] = []
    cycle_ids: list[str] = []
    slots: list[int] = []

    for customer_id, owned in by_customer.items():
        owned.sort(key=lambda c: c.due_at)
        for index, cycle in enumerate(owned):
            if not _cycle_failed(cycle):
                continue

            history = build_history(owned[:index])
            present = presence_of(cycle)
            legal = legal_of(cycle)

            for hour in np.flatnonzero(legal).tolist():
                local = (cycle.due_at + timedelta(hours=int(hour))).astimezone(IST)
                key = (int(hour) // 24, hour_band(local.hour + local.minute / 60.0))
                rows.append(
                    hourly_features(
                        cycle,
                        history,
                        int(hour),
                        segment_prior_hazard=(prior or {}).get(key, default_prior),
                    )
                )
                labels.append(int(present[hour]))
                groups.append(customer_id)
                cycle_ids.append(cycle.cycle_id)
                slots.append(int(hour))

    if not rows:
        raise ValueError("no failed cycles in population — nothing to train on")

    return Dataset(
        x=np.asarray(rows, dtype=np.float64),
        y=np.asarray(labels, dtype=np.int64),
        groups=np.asarray(groups),
        cycle_ids=np.asarray(cycle_ids),
        slots=np.asarray(slots, dtype=np.int64),
    )


def group_split(
    dataset: Dataset, *, holdout_fraction: float = 0.3, seed: int = 0
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.bool_]]:
    """Split by customer, not by row.

    Returns boolean masks (train, holdout). §21's whole claim is about
    generalising to a customer whose payday has not been seen; a row-level split
    would put the same customer's other cycles in training and quietly answer an
    easier question.
    """
    if not 0.0 < holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be in (0, 1)")

    customers = np.unique(dataset.groups)
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(customers)
    n_holdout = max(1, round(len(shuffled) * holdout_fraction))
    holdout_customers = set(shuffled[:n_holdout].tolist())

    is_holdout = np.asarray([g in holdout_customers for g in dataset.groups], dtype=np.bool_)
    return ~is_holdout, is_holdout


def subset(dataset: Dataset, mask: npt.NDArray[np.bool_]) -> Dataset:
    """The rows a mask selects, keeping every parallel array aligned."""
    return Dataset(
        x=dataset.x[mask],
        y=dataset.y[mask],
        groups=dataset.groups[mask],
        cycle_ids=dataset.cycle_ids[mask],
        slots=dataset.slots[mask],
    )
