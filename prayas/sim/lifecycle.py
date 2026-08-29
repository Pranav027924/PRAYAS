"""Mandate lifecycles and revocation (ADR-060; Master Spec §22, §38).

Until now the simulator produced independent cycles and revoked nothing:
`revocation_beta` had been declared and validated since Phase 3 but was never
read. That left three Phase 10 criteria unachievable — there were no mandate
deaths to calibrate against, no time axis to draw survival curves over, and no
history for §24.3's "chronic" test to count.

**Revocation is a hazard over time, not a coin flip per failure.** §22 defines
`r(t) = P(revoked at t | alive at t)` and lists "days since last successful
debit" among its drivers. That is what makes waiting expensive, and the absence
of any such cost is FINDING-P8-01's root cause. A per-failure Bernoulli would
produce deaths but leave delay free.

Gated behind `SimConfig.cycles_per_mandate`, which defaults to 1. Every closed
phase's recorded numbers therefore stay reproducible; Phase 10 opts in.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import numpy as np

from prayas.sim.config import SimConfig
from prayas.sim.generate import SimulatedCycle, generate_cycle, outage_calendar

#: §22 baseline per-day revocation hazard for a healthy, paying mandate.
#: Small: most mandates die from neglect, not spontaneously.
BASE_DAILY_HAZARD: Final = 0.00035

#: §22 "Consecutive failed cycles ↑" — multiplier per consecutive failure.
CONSECUTIVE_FAILURE_MULTIPLIER: Final = 1.55

#: §22 "Days since last successful debit ↑". Per-day growth, and the term that
#: makes *waiting* costly rather than only *attempting*.
DAYS_UNPAID_MULTIPLIER_PER_DAY: Final = 0.008

#: §22 "Tenure, successful cycle count ↓" — protection per settled cycle.
TENURE_PROTECTION_PER_CYCLE: Final = 0.90

#: §22 "Rail (UPI Autopay = one-tap revocation)" — rail-specific baseline.
RAIL_BASELINE: Final[dict[str, float]] = {
    "upi_autopay": 1.6,  # one-tap revocation in the UPI app
    "card_emandate": 1.0,
    "enach": 0.7,  # revoking needs a bank instruction
}


@dataclass(frozen=True, slots=True)
class MandateState:
    """The §22 features, as they stand entering a cycle."""

    rail: str
    consecutive_failures: int
    days_since_success: float
    successful_cycles: int
    messages_30d: int = 0

    def daily_hazard(self, config: SimConfig) -> float:
        """§22's `r(t)`, assembled from the features it names.

        Multiplicative rather than additive so the drivers compound the way
        risk actually does: a mandate that has failed three times *and* gone a
        month unpaid is worse than the sum of those two states.
        """
        hazard = BASE_DAILY_HAZARD * RAIL_BASELINE.get(self.rail, 1.0)
        hazard *= CONSECUTIVE_FAILURE_MULTIPLIER**self.consecutive_failures
        hazard *= 1.0 + DAYS_UNPAID_MULTIPLIER_PER_DAY * self.days_since_success
        hazard *= TENURE_PROTECTION_PER_CYCLE ** min(self.successful_cycles, 24)
        # §22 "Messages sent in trailing 30 days (fatigue) ↑" — Phase 9's
        # notification volume feeds retention risk, which is why §24.6 insists
        # a system must be able to choose silence.
        hazard *= (1.0 + config.fatigue_decay) ** self.messages_30d
        return float(min(hazard, 0.95))


@dataclass(frozen=True, slots=True)
class MandateLifecycle:
    """One mandate observed over time, for survival analysis.

    `died` distinguishes a revocation from an observation that simply ended —
    the censoring distinction §34 requires, and the reason a raw revocation
    rate would be biased by cohort age.
    """

    mandate_id: str
    customer_id: str
    rail: str
    cycles: list[SimulatedCycle]
    died: bool
    duration_days: float

    @property
    def observed_cycles(self) -> int:
        return len(self.cycles)


def _revocation_roll(mandate_id: str, seq_no: int) -> float:
    """Uniform in [0, 1), deterministic in the mandate and cycle.

    Drawn from a hash rather than the generator's stream so adding a cycle to
    one mandate cannot shift another's draws — the same reproducibility
    property ADR-029's spawned streams give.
    """
    digest = hashlib.sha256(f"revoke:{mandate_id}:{seq_no}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(1 << 64)


def _cycle_failed(cycle: SimulatedCycle) -> bool:
    return any(e.get("body", {}).get("event") == "payment.failed" for e in cycle.observables)


def generate_lifecycle(
    config: SimConfig,
    rng: np.random.Generator,
    *,
    tenant_id: str,
    mandate_index: int,
    calendar: dict[str, list[tuple[datetime, datetime]]] | None = None,
) -> MandateLifecycle:
    """One mandate billed until it is revoked or the observation window ends."""
    cycles: list[SimulatedCycle] = []
    state = MandateState(
        rail="upi_autopay", consecutive_failures=0, days_since_success=0.0, successful_cycles=0
    )
    died = False
    duration_days = 0.0
    mandate_id = f"sub_{mandate_index:07d}"

    for seq_no in range(config.cycles_per_mandate):
        cycle = generate_cycle(
            config,
            rng,
            tenant_id=tenant_id,
            index=mandate_index * config.cycles_per_mandate + seq_no,
            calendar=calendar,
        )
        # Successive cycles of one mandate bill a month apart and share an id.
        cycle = SimulatedCycle(
            tenant_id=cycle.tenant_id,
            mandate_id=mandate_id,
            customer_id=f"cust_{mandate_index // max(config.cycles_per_customer, 1):07d}",
            cycle_id=f"{mandate_id}_inv{seq_no:03d}",
            rail=cycle.rail if seq_no == 0 else state.rail,
            issuer=cycle.issuer,
            amount_paise=cycle.amount_paise,
            due_at=cycle.due_at + timedelta(days=30 * seq_no),
            next_billing_at=cycle.due_at + timedelta(days=30 * (seq_no + 1)),
            observables=cycle.observables,
            truth=cycle.truth,
        )
        cycles.append(cycle)

        failed = _cycle_failed(cycle)
        state = MandateState(
            rail=cycle.rail,
            consecutive_failures=state.consecutive_failures + 1 if failed else 0,
            days_since_success=state.days_since_success + 30.0 if failed else 0.0,
            successful_cycles=state.successful_cycles + (0 if failed else 1),
            messages_30d=state.messages_30d,
        )

        # §22: revocation is a hazard over the days between billings, not a
        # single draw at the moment of failure.
        daily = state.daily_hazard(config)
        survive_month = (1.0 - daily) ** 30
        duration_days += 30.0
        if _revocation_roll(mandate_id, seq_no) > survive_month:
            died = True
            break

    return MandateLifecycle(
        mandate_id=mandate_id,
        customer_id=cycles[0].customer_id,
        rail=cycles[0].rail,
        cycles=cycles,
        died=died,
        duration_days=duration_days,
    )


def generate_lifecycles(
    config: SimConfig, *, seed: int, tenant_id: str, mandates: int
) -> list[MandateLifecycle]:
    """A population of mandates observed over time."""
    if mandates <= 0:
        raise ValueError(f"mandates must be positive, got {mandates}")

    calendar = outage_calendar(config, seed=seed)
    root = np.random.SeedSequence(seed)
    return [
        generate_lifecycle(
            config,
            np.random.default_rng(child),
            tenant_id=tenant_id,
            mandate_index=index,
            calendar=calendar,
        )
        for index, child in enumerate(root.spawn(mandates))
    ]


def flatten(lifecycles: list[MandateLifecycle]) -> list[SimulatedCycle]:
    """Every cycle across every mandate, for the existing cycle-level paths."""
    return [cycle for lifecycle in lifecycles for cycle in lifecycle.cycles]
