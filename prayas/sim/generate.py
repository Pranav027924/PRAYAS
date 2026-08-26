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


@dataclass(frozen=True, slots=True)
class GroundTruth:
    """What the process knows. Never observable, never a feature (ADR-030)."""

    true_cause: str
    true_funding_time: datetime | None
    issuer_state: str
    payday_archetype: str
    masked_as_05: bool


@dataclass(frozen=True, slots=True)
class SimulatedCycle:
    """One cycle: the observable stream, and separately what really happened."""

    tenant_id: str
    mandate_id: str
    customer_id: str
    cycle_id: str
    rail: str
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


def _issuer_outage_days(config: SimConfig, rng: np.random.Generator) -> set[int]:
    """Poisson outage onsets, LogNormal durations (§38)."""
    onsets = int(rng.poisson(config.outage_lambda * config.horizon_days))
    days: set[int] = set()
    for _ in range(onsets):
        start = int(rng.integers(0, config.horizon_days))
        mu, sigma = config.outage_duration
        minutes = float(rng.lognormal(mu, sigma))
        # A long outage spans days; a short one affects only its own day.
        span = max(1, int(minutes // (60 * 24)) + 1)
        days.update(range(start, min(start + span, config.horizon_days)))
    return days


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
) -> SimulatedCycle:
    """One cycle with its full observable stream and its ground truth."""
    archetype = _choose(rng, config.payday_mix)
    rail = _choose(rng, config.rail_mix)

    customer_id = f"cust_{index:07d}"
    mandate_id = f"sub_{index:07d}"
    cycle_id = f"inv_{index:07d}"

    low, high = config.amount_paise_range
    amount_paise = int(rng.integers(low, high + 1))

    due_at = ORIGIN + timedelta(days=int(rng.integers(0, 28)), hours=int(rng.integers(0, 24)))
    next_billing_at = due_at + timedelta(days=30)

    funding_days = _payday_offsets(archetype, rng, config.horizon_days)
    outage_days = _issuer_outage_days(config, rng)

    # The first debit lands on day 0. Funded iff money arrived by then, and —
    # unless non_absorbing — it stays funded once it has arrived.
    funded_by_day0 = any(day <= 0 for day in funding_days)
    if config.non_absorbing and funded_by_day0:
        # §38: money arrives AND is spent. §21 assumes funding is absorbing;
        # this is that assumption's stated limitation, made measurable.
        funded_by_day0 = bool(rng.random() > 0.5)

    issuer_down = 0 in outage_days
    issuer_state = ISSUER_DEGRADED if issuer_down else "healthy"

    cause = _classify(config, rng, funded=funded_by_day0, issuer_down=issuer_down)
    succeeded = cause is None

    masked = bool(cause in (NO_FUNDS, "fraud_hold") and rng.random() < config.mask_05_rate)

    first_funding = min((d for d in funding_days if d >= 0), default=None)
    true_funding_time = (
        due_at + timedelta(days=first_funding) if first_funding is not None else None
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
        amount_paise=amount_paise,
        due_at=due_at,
        next_billing_at=next_billing_at,
        observables=observables,
        truth=GroundTruth(
            true_cause=cause or "none",
            true_funding_time=true_funding_time,
            issuer_state=issuer_state,
            payday_archetype=archetype,
            masked_as_05=masked,
        ),
    )


def _classify(
    config: SimConfig, rng: np.random.Generator, *, funded: bool, issuer_down: bool
) -> str | None:
    """The latent cause of failure, or None if the debit succeeds.

    Order matters and is deliberate: an issuer outage fails the debit regardless
    of the customer's balance, which is exactly why `issuer_wilson_lower` and
    `peer_success_rate` discriminate in §20's feature table.
    """
    if issuer_down:
        return ISSUER_DEGRADED
    if not funded:
        return NO_FUNDS

    # Funded and the issuer is healthy: only the residual causes remain.
    roll = float(rng.random())
    if roll < config.base_failure_rate * 0.25:
        return LIMIT_BREACH
    if roll < config.base_failure_rate * 0.4:
        return "fraud_hold"
    if roll < config.base_failure_rate * 0.45:
        return MANDATE_DEAD
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
    root = np.random.SeedSequence(seed)
    return [
        generate_cycle(
            config,
            np.random.default_rng(child),
            tenant_id=tenant_id,
            index=index,
        )
        for index, child in enumerate(root.spawn(cycles))
    ]
