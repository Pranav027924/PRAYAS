"""Generative process with ground truth (Master Spec §38, §20, §21).

The point is labels. Real data can never tell you *why* a debit failed — code 05
covers insufficient funds and issuer trouble alike — so cause inference can only
be validated against a process that knows the answer (§20).

**Determinism (ADR-029).** Every draw comes from an explicitly passed
`numpy.random.Generator`. Per-entity streams are spawned from one
`SeedSequence`, so adding a customer shifts nobody else's draws and the same
seed reproduces byte-identical output.

**Leakage (ADR-030).** `SimulatedCycle` separates `observables` from `truth`.
Only observables become events; truth goes to a table the app role cannot read.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import numpy as np

from prayas.sim.config import (
    CHRONICALLY_DRY,
    DO_NOT_HONOR,
    FRAUD_HOLD,
    GIG_IRREGULAR,
    ISSUER_DEGRADED,
    LIMIT_BREACH,
    MANDATE_DEAD,
    NO_FUNDS,
    SALARIED_1ST,
    SALARIED_7TH,
    SimConfig,
)

#: Deterministic epoch, so a run's timestamps depend only on the seed.
ORIGIN: Final = datetime(2026, 1, 1, tzinfo=UTC)

#: Decline codes emitted for causes other than no_funds, when not masked.
CAUSE_CODES: Final[dict[str, str]] = {
    ISSUER_DEGRADED: "91",  # issuer unavailable
    LIMIT_BREACH: "61",  # exceeds withdrawal limit
    MANDATE_DEAD: "57",
}

#: §20 — fraud_hold surfaces as 05 too; it is one of the reasons 05 is useless
#: without context.
FRAUD_CODE: Final = DO_NOT_HONOR

#: ADR-067 — the issuer roster. Outages are a property of an *issuer over time*,
#: shared by every customer banking there. Until now each cycle drew its own
#: private outage calendar, which meant no two cycles could ever agree that the
#: issuer was down — so §20's `peer_success_rate` ("other customers, same
#: issuer, same 5-min window") was uncomputable, and Phase 11's nowcast had no
#: population-level event to detect.
#: ADR-069 — spread of per-customer fraud propensity. Wide enough that the
#: sibling failure rate separates the prone from the ordinary, narrow enough
#: that no customer is certain to be held.
_FRAUD_FLOOR: Final = 0.15
_FRAUD_CEILING: Final = 3.0

#: ADR-069 — the range a customer's debit ceiling is drawn from, integer paise.
#: Spans the amount range so `amount / limit` covers both comfortable and
#: breaching debits rather than sitting entirely on one side.
_LIMIT_FLOOR_PAISE: Final = 100_000
_LIMIT_CEILING_PAISE: Final = 600_000

ISSUERS: Final[tuple[str, ...]] = (
    "HDFC",
    "ICICI",
    "SBI",
    "AXIS",
    "KOTAK",
    "PNB",
    "BOB",
    "IDFC",
)


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """What the process knows. Never observable, never a feature (ADR-030)."""

    true_cause: str
    true_funding_time: datetime | None
    issuer_state: str
    payday_archetype: str
    masked_as_05: bool
    #: ADR-073 — half-open [start, end) intervals in which the account actually
    #: held money. Under the default absorbing dynamics there is exactly one,
    #: open-ended: money arrives and stays. Under `non_absorbing` each payday
    #: gives a bounded window, because §21's "money arrives and is spent" is
    #: only a real limitation if the money can leave again.
    funding_windows: tuple[tuple[datetime, datetime], ...] = ()


@dataclass(frozen=True, slots=True)
class SimulatedCycle:
    """One cycle: the observable stream, and separately what really happened."""

    tenant_id: str
    mandate_id: str
    customer_id: str
    cycle_id: str
    rail: str
    #: ADR-067 — which issuer holds the account. Observable: it is on the
    #: payment. Shared across customers, which is what makes an outage a
    #: detectable event rather than a private coincidence.
    issuer: str
    amount_paise: int
    due_at: datetime
    next_billing_at: datetime
    observables: list[dict[str, Any]]
    truth: GroundTruth


def _payday_offsets(archetype: str, rng: np.random.Generator, horizon: int) -> list[int]:
    """Days from cycle start on which money arrives, by archetype.

    Modelled as *arrival days* rather than a probability, because §21 wants a
    time-indexed curve and the simulator must be able to say exactly when funds
    appeared for the confusion matrix to mean anything.
    """
    if archetype == SALARIED_1ST:
        return [int(d) for d in range(0, horizon, 30)]
    if archetype == SALARIED_7TH:
        return [int(d) for d in range(6, horizon, 30)]
    if archetype == GIG_IRREGULAR:
        # Irregular but not rare: several small arrivals across the horizon.
        count = int(rng.integers(2, 6))
        return sorted({int(d) for d in rng.integers(0, horizon, size=count)})
    if archetype == CHRONICALLY_DRY:
        # Occasionally funded, usually not — the population §1 says no retry
        # schedule can ever help, and where stopping is the right answer.
        return [int(rng.integers(0, horizon))] if rng.random() < 0.25 else []
    raise ValueError(f"unknown archetype: {archetype}")


def _funding_hour(archetype: str, rng: np.random.Generator) -> int:
    """Hours after the day boundary at which money actually lands.

    Funding does not arrive at midnight sharp. Modelling it at whole-day
    resolution makes the hazard curve a comb — a handful of spikes with zeros
    between them — and between spikes `S(t)` is flat, so `p(t\'|t) = 1 - S(t\')/S(t)`
    cannot separate adjacent slots and the sequencer has nothing to act on.

    Salary credits clear in a tight early band; gig income arrives across the
    working day. Bounded to under 24 hours so the *day* index is unchanged,
    which keeps §21's day-level properties and Phase 4\'s tests intact.
    """
    if archetype in (SALARIED_1ST, SALARIED_7TH):
        return int(rng.integers(0, 8))
    return int(rng.integers(0, 24))


#: Distinguishes the issuer-calendar stream from the per-cycle streams, so the
#: two cannot collide for a given seed.
_ISSUER_STREAM: Final = 0x1557E

#: An issuer's outage windows as half-open [start, end) UTC intervals.
OutageCalendar = dict[str, list[tuple[datetime, datetime]]]

#: Days of `due_at` spread in `generate_cycle`, and therefore the span an
#: outage calendar has to cover for a cycle to be able to land inside one.
_DUE_SPREAD_DAYS: Final = 28


def _issuer_outages(
    config: SimConfig, rng: np.random.Generator, *, span_days: int
) -> list[tuple[datetime, datetime]]:
    """Poisson onsets, LogNormal durations in minutes (§38).

    Minute resolution is kept rather than rounded up to whole days. §38 gives
    the duration as `LogNormal(4.0, 0.8)` minutes — a median of about 54 — so
    rounding to a day turned every outage into a 24-hour one and erased exactly
    the timescale Phase 11's nowcast is asked to resolve.
    """
    onsets = int(rng.poisson(config.outage_lambda * span_days))
    mu, sigma = config.outage_duration

    windows: list[tuple[datetime, datetime]] = []
    for _ in range(onsets):
        start = ORIGIN + timedelta(
            days=int(rng.integers(0, span_days)),
            minutes=int(rng.integers(0, 24 * 60)),
        )
        minutes = float(rng.lognormal(mu, sigma))
        windows.append((start, start + timedelta(minutes=minutes)))
    return sorted(windows)


def outage_calendar(
    config: SimConfig, *, seed: int, span_days: int | None = None
) -> OutageCalendar:
    """Every issuer's outage windows for a run.

    Drawn from a stream keyed on the *seed alone*, never on how many cycles are
    being generated, so ADR-029's prefix property survives: a 100-cycle run and
    a 10,000-cycle run see the same issuers going down at the same moments.
    """
    span = span_days if span_days is not None else _DUE_SPREAD_DAYS + config.horizon_days
    root = np.random.SeedSequence([seed, _ISSUER_STREAM])
    return {
        issuer: _issuer_outages(config, np.random.default_rng(child), span_days=span)
        for issuer, child in zip(ISSUERS, root.spawn(len(ISSUERS)), strict=True)
    }


def is_down(calendar: OutageCalendar, issuer: str, at: datetime) -> bool:
    """Whether `issuer` was inside an outage window at `at`."""
    return any(start <= at < end for start, end in calendar.get(issuer, ()))


def _emit_code(cause: str, masked: bool) -> str:
    """The decline code an issuer would actually return.

    §20: roughly half of `no_funds` surfaces as 05 rather than 51, which is what
    makes cause inference necessary at all.
    """
    if masked:
        return DO_NOT_HONOR
    if cause == NO_FUNDS:
        return "51"
    return CAUSE_CODES.get(cause, DO_NOT_HONOR)


def _choose_stable(key: str, mix: dict[str, float]) -> str:
    """Pick from a mixture deterministically in `key`, not from the RNG stream.

    ADR-058 made customers recur, but a payday archetype is a property of the
    *person*: someone salaried on the 1st is salaried on the 1st next month
    too. Drawing it per cycle left each customer's history uninformative about
    their own future — per-customer prediction measured 20% *worse* than a
    population constant, because there was nothing consistent to learn.

    Keyed on the customer rather than sampled so every cycle that customer
    holds resolves to the same archetype, without consuming RNG draws that
    would shift the per-cycle streams (ADR-029).
    """
    digest = hashlib.sha256(f"archetype:{key}".encode()).digest()
    roll = int.from_bytes(digest[:8], "big") / float(1 << 64)
    cumulative = 0.0
    for name, weight in sorted(mix.items()):
        cumulative += weight
        if roll < cumulative:
            return name
    return sorted(mix)[-1]


def _stable_index(key: str, modulus: int) -> int:
    """A deterministic index in `key` — the same trick as `_choose_stable`.

    Used for the issuer, which is a property of the person and not of the
    cycle, and which must not consume an RNG draw (ADR-029).
    """
    digest = hashlib.sha256(f"issuer:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % modulus


def _choose(rng: np.random.Generator, mix: dict[str, float]) -> str:
    keys = sorted(mix)  # sorted so the mapping from draw to key is stable
    weights = np.array([mix[k] for k in keys], dtype=float)
    return str(rng.choice(keys, p=weights / weights.sum()))


def generate_cycle(
    config: SimConfig,
    rng: np.random.Generator,
    *,
    tenant_id: str,
    index: int,
    calendar: OutageCalendar | None = None,
) -> SimulatedCycle:
    """One cycle with its full observable stream and its ground truth.

    `calendar` carries the shared issuer outage windows (ADR-067). It is
    optional only so a caller can generate one cycle in isolation; every
    population path passes one, because without it "the issuer was down" is a
    private fact and no peer can corroborate it.
    """
    customer_index = index // max(config.cycles_per_customer, 1)
    customer_id = f"cust_{customer_index:07d}"

    # Stable per customer (ADR-058): the person's payday pattern, not the
    # cycle's. Rail stays per-mandate, which is correct — one customer may hold
    # a UPI mandate with one merchant and a card mandate with another.
    archetype = _choose_stable(customer_id, config.payday_mix)
    rail = _choose(rng, config.rail_mix)
    # A person banks with one bank, so the issuer is stable per customer for
    # the same reason ADR-058 made the payday archetype stable.
    issuer = ISSUERS[_stable_index(customer_id, len(ISSUERS))]

    mandate_id = f"sub_{index:07d}"
    cycle_id = f"inv_{index:07d}"

    low, high = config.amount_paise_range
    amount_paise = int(rng.integers(low, high + 1))

    due_at = ORIGIN + timedelta(days=int(rng.integers(0, 28)), hours=int(rng.integers(0, 24)))
    next_billing_at = due_at + timedelta(days=30)

    funding_days = _payday_offsets(archetype, rng, config.horizon_days)

    # The first debit lands on day 0. Funded iff money arrived by then, and —
    # unless non_absorbing — it stays funded once it has arrived.
    funded_by_day0 = any(day <= 0 for day in funding_days)
    if config.non_absorbing and funded_by_day0:
        # §38: money arrives AND is spent. §21 assumes funding is absorbing;
        # this is that assumption's stated limitation, made measurable.
        funded_by_day0 = bool(rng.random() > 0.5)

    if calendar is None:
        calendar = {issuer: _issuer_outages(config, rng, span_days=config.horizon_days)}
    issuer_down = is_down(calendar, issuer, due_at)
    issuer_state = ISSUER_DEGRADED if issuer_down else "healthy"

    cause = _classify(
        config,
        rng,
        funded=funded_by_day0,
        issuer_down=issuer_down,
        amount_paise=amount_paise,
        limit_paise=customer_limit_paise(customer_id),
        fraud_propensity=fraud_propensity(customer_id),
    )
    succeeded = cause is None

    # ADR-065: several causes hide behind 05, at different rates, so the
    # bucket is a genuine mixture rather than one component wearing a disguise.
    masked = bool(cause is not None and rng.random() < config.mask_probability(cause))

    first_funding = min((d for d in funding_days if d >= 0), default=None)
    funding_hour = _funding_hour(archetype, rng)
    true_funding_time = (
        due_at + timedelta(days=first_funding, hours=funding_hour)
        if first_funding is not None
        else None
    )
    funding_windows = _funding_windows(
        config, funding_days, due_at=due_at, funding_hour=funding_hour
    )

    observables = _emit_events(
        mandate_id=mandate_id,
        cycle_id=cycle_id,
        due_at=due_at,
        next_billing_at=next_billing_at,
        amount_paise=amount_paise,
        succeeded=succeeded,
        code=None if cause is None else _emit_code(cause, masked),
    )

    return SimulatedCycle(
        tenant_id=tenant_id,
        mandate_id=mandate_id,
        customer_id=customer_id,
        cycle_id=cycle_id,
        rail=rail,
        issuer=issuer,
        amount_paise=amount_paise,
        due_at=due_at,
        next_billing_at=next_billing_at,
        observables=observables,
        truth=GroundTruth(
            true_cause=cause or "none",
            true_funding_time=true_funding_time,
            funding_windows=funding_windows,
            issuer_state=issuer_state,
            payday_archetype=archetype,
            masked_as_05=masked,
        ),
    )


def customer_limit_paise(customer_id: str) -> int:
    """A stable per-customer debit ceiling, in integer paise (ADR-069).

    §20 says `amount / p75(customer successful debits)` discriminates
    `limit_breach`. That is only true if a limit breach actually depends on the
    amount — so the customer has a ceiling, fixed for the person the way
    ADR-058 fixed their payday, and a debit's risk of breaching it is a
    function of how close it comes.
    """
    digest = hashlib.sha256(f"limit:{customer_id}".encode()).digest()
    roll = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return int(_LIMIT_FLOOR_PAISE + roll * (_LIMIT_CEILING_PAISE - _LIMIT_FLOOR_PAISE))


def fraud_propensity(customer_id: str) -> float:
    """How readily this account trips a fraud rule (ADR-069).

    Stable for the person, like their payday (ADR-058) and their ceiling. It has
    to be a property of the account rather than of the debit, or §20's
    `sibling_failure_rate` — "this customer's other mandates, same window" —
    has nothing to correlate with and cannot rule the customer side in.
    """
    digest = hashlib.sha256(f"fraud:{customer_id}".encode()).digest()
    roll = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return _FRAUD_FLOOR + roll * (_FRAUD_CEILING - _FRAUD_FLOOR)


def _funding_windows(
    config: SimConfig,
    funding_days: list[int],
    *,
    due_at: datetime,
    funding_hour: int,
) -> tuple[tuple[datetime, datetime], ...]:
    """When the account actually held money (ADR-073).

    Absorbing (the default): one open-ended window from the first arrival, which
    is exactly the old behaviour expressed as an interval.

    Non-absorbing: one bounded window per payday. This is what §21 means by
    "money arrives and is spent", and it is the difference between a retry
    schedule that can miss and one that cannot.
    """
    arrivals = sorted(day for day in funding_days if day >= 0)
    if not arrivals:
        return ()

    if not config.non_absorbing:
        start = due_at + timedelta(days=arrivals[0], hours=funding_hour)
        return ((start, start + timedelta(days=365)),)

    dwell = timedelta(hours=config.funds_dwell_hours)
    return tuple(
        (
            due_at + timedelta(days=day, hours=funding_hour),
            due_at + timedelta(days=day, hours=funding_hour) + dwell,
        )
        for day in arrivals
    )


def _classify(
    config: SimConfig,
    rng: np.random.Generator,
    *,
    funded: bool,
    issuer_down: bool,
    amount_paise: int,
    limit_paise: int,
    fraud_propensity: float,
) -> str | None:
    """The latent cause of failure, or None if the debit succeeds.

    Order matters and is deliberate: an issuer outage fails the debit regardless
    of the customer's balance, which is exactly why `issuer_wilson_lower` and
    `peer_success_rate` discriminate in §20's feature table.

    **ADR-069 — the residual causes depend on observables.** They used to be
    drawn from a bare uniform roll, which meant `limit_breach` was independent
    of the amount and `fraud_hold` independent of velocity. §20 asserts the
    opposite for both, so every model was being asked to recover a signal the
    simulator had deleted, and the confusion matrix measured the simulator's
    arbitrariness rather than the model's skill — the same defect ADR-035
    recorded for code 05.

    The *total* residual failure probability is held at its previous value, so
    only the split between causes moves, not the overall failure rate.
    """
    if issuer_down:
        return ISSUER_DEGRADED
    if not funded:
        return NO_FUNDS

    # Funded and the issuer is healthy: only the residual causes remain.
    budget = config.base_failure_rate * 0.45

    # A debit far under the ceiling rarely breaches it; one at or over it often
    # does. Squared so the rise is concentrated near the limit rather than
    # spread evenly, which is how a real ceiling behaves.
    closeness = min(3.0, (amount_paise / limit_paise) ** 2)
    # §20 lists `sibling_failure_rate` as the feature that "rules customer-side
    # in". A fraud rule fires on the *account*, not on one debit, so a
    # fraud-prone customer's mandates all suffer — which is precisely what
    # makes the sibling rate observable evidence rather than a coincidence.
    velocity = fraud_propensity

    weights = {
        LIMIT_BREACH: 0.25 * closeness,
        FRAUD_HOLD: 0.15 * velocity,
        MANDATE_DEAD: 0.05,
    }
    total = sum(weights.values())
    scale = budget / total if total > 0 else 0.0

    roll = float(rng.random())
    cumulative = 0.0
    for cause, weight in weights.items():
        cumulative += weight * scale
        if roll < cumulative:
            return cause
    return None


def _emit_events(
    *,
    mandate_id: str,
    cycle_id: str,
    due_at: datetime,
    next_billing_at: datetime,
    amount_paise: int,
    succeeded: bool,
    code: str | None,
) -> list[dict[str, Any]]:
    """The observable webhook stream. Contains nothing a model may not see."""
    events: list[dict[str, Any]] = [
        _event(
            "subscription.authenticated",
            mandate_id=mandate_id,
            cycle_id=cycle_id,
            occurred_at=due_at - timedelta(days=1),
            amount_paise=amount_paise,
            next_billing_at=next_billing_at,
        )
    ]
    events.append(
        _event(
            "payment.captured" if succeeded else "payment.failed",
            mandate_id=mandate_id,
            cycle_id=cycle_id,
            occurred_at=due_at,
            amount_paise=amount_paise,
            next_billing_at=next_billing_at,
            error_code=code,
        )
    )
    return events


def _event(
    event_type: str,
    *,
    mandate_id: str,
    cycle_id: str,
    occurred_at: datetime,
    amount_paise: int,
    next_billing_at: datetime,
    error_code: str | None = None,
) -> dict[str, Any]:
    """A Razorpay-shaped body, matching what `envelope.parse` expects."""
    entity: dict[str, Any] = {
        "id": f"pay_{cycle_id}_{event_type}",
        "amount": amount_paise,
        "invoice_id": cycle_id,
    }
    if error_code:
        entity["error_code"] = error_code

    body = {
        "entity": "event",
        "event": event_type,
        "created_at": int(occurred_at.timestamp()),
        "contains": ["payment", "subscription"],
        "payload": {
            "subscription": {
                "entity": {"id": mandate_id, "current_end": int(next_billing_at.timestamp())}
            },
            "payment": {"entity": entity},
        },
    }
    # Event ids are derived, not random: a rerun with the same seed must produce
    # the same ids or dedupe would silently change the projected state.
    digest = hashlib.sha256(
        f"{mandate_id}:{cycle_id}:{event_type}:{int(occurred_at.timestamp())}".encode()
    ).hexdigest()[:24]
    return {"event_id": f"evt_{digest}", "body": body, "occurred_at": occurred_at}


def generate(config: SimConfig, *, seed: int, tenant_id: str, cycles: int) -> list[SimulatedCycle]:
    """Generate `cycles` cycles reproducibly.

    Each cycle draws from its own spawned stream, so cycle N's output does not
    depend on how many cycles preceded it — which is what makes a 100-cycle run
    a genuine prefix of a 10,000-cycle run.
    """
    calendar = outage_calendar(config, seed=seed)
    root = np.random.SeedSequence(seed)
    return [
        generate_cycle(
            config,
            np.random.default_rng(child),
            tenant_id=tenant_id,
            index=index,
            calendar=calendar,
        )
        for index, child in enumerate(root.spawn(cycles))
    ]
