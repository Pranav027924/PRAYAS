"""The Customer Payment Profile (Master Spec §26).

§29 states the commercial claim plainly: "the profile tier compounds. A
merchant's hundredth cycle with a customer is meaningfully better-informed than
their first, and that advantage cannot be copied by a competitor who arrives
later, because it is not in the model weights — it is in the accumulated
per-customer posterior."

This module is that posterior, and the update rules §26 specifies for it.

**Everything here is per tenant.** §27 is explicit that "individual memory is
strictly tenant-scoped", because DPDP purpose limitation makes a cross-merchant
behavioural profile "the kind of thing that looks like a feature and is actually
a liability". The `tenant_id` on every profile is not decoration; it is the
boundary Invariant 8 draws, and `prayas.memory.store` filters on it in every
query.

**Three decays, three different jobs.** They are separate on purpose:

* the *posterior* decays so that a payday that moved last quarter stops
  outvoting the one in use now;
* a *declared hint* decays to nothing over 180 days, because "salary comes on
  the 5th" told to you eight months ago is not evidence any more;
* *fatigue* decays with a half-life of about two weeks, because a customer
  messaged twice yesterday and a customer messaged twice last month are not in
  the same state.

Collapsing them into one rate would be simpler and would model none of them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final

#: §26 — "older observations lose influence". Applied to the whole posterior
#: before each new observation is added.
PAYDAY_DECAY: Final = 0.97

#: §26 — declared values "decay to zero over 180 days".
DECLARED_TTL_DAYS: Final = 180.0

#: §26 — fatigue "decays exponentially with a half-life of roughly two weeks".
FATIGUE_HALF_LIFE_DAYS: Final = 14.0

#: Days of the month a payday posterior ranges over. 31 rather than 30 because
#: a salary credited on the 31st is a real thing and dropping it would put its
#: mass silently on the wrong day.
DAYS_IN_MONTH: Final = 31

#: §37 — "build per-customer profiles where at least three cycles exist; shrink
#: hard toward segment priors below that".
MIN_CYCLES_FOR_PROFILE: Final = 3

#: Shrinkage strength toward the segment prior, matching §21's `kappa ~ 5`.
DEFAULT_KAPPA: Final = 5.0


class ProfileError(ValueError):
    """The profile is not a coherent state."""


@dataclass(frozen=True, slots=True)
class CustomerPaymentProfile:
    """§26's accumulating asset, as a value rather than a mutable record.

    Frozen because every update is an event with a time attached: `update_*`
    returns a new profile, so a caller cannot half-apply one and leave the
    posterior normalised against a decay that was never applied.
    """

    tenant_id: str
    customer_id: str

    # ── liquidity model ────────────────────────────────────────────────────
    #: Day-of-month -> **unnormalised decayed evidence** (ADR-077).
    #:
    #: §26's pseudocode normalises inside the update, which collapses the
    #: accumulated count: each new observation then takes ~51% of the mass
    #: whatever the history, one cycle yields total certainty, and the 0.97
    #: decay does nothing. Counts are kept raw here and normalised on read, so
    #: the decay means what §26 says it means and §29's compounding is real.
    #: Read it through `normalised_posterior()`, never directly.
    payday_posterior: dict[int, float] = field(default_factory=dict)
    #: **LLM-parsed from an inbound reply (§25).** Untrusted data with a bounded
    #: effect: it shifts a prior, it never becomes an instruction, and it
    #: expires. Invariant 9.
    declared_funding_day: int | None = None
    declared_at: datetime | None = None
    typical_amount_p75: int | None = None
    fail_success_lag_days: tuple[float, ...] = ()

    # ── engagement model ───────────────────────────────────────────────────
    preferred_channel: str | None = None
    engagement_hours: tuple[int, ...] = ()
    messages_30d: int = 0
    fatigue_score: float = 0.0
    last_contact_at: datetime | None = None

    # ── risk model ─────────────────────────────────────────────────────────
    revocation_events: int = 0
    consecutive_failures: int = 0
    tenure_cycles: int = 0

    # ── governance ─────────────────────────────────────────────────────────
    consent_ref: str | None = None
    consent_withdrawn: bool = False
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ProfileError("a profile without a tenant is not tenant-scoped (§27)")
        if not self.customer_id:
            raise ProfileError("customer_id is required")
        for day in self.payday_posterior:
            if not 1 <= day <= DAYS_IN_MONTH:
                raise ProfileError(f"day-of-month {day} is out of range")
        if any(mass < 0 for mass in self.payday_posterior.values()):
            raise ProfileError("payday posterior has negative evidence")
        if self.declared_funding_day is not None and not (
            1 <= self.declared_funding_day <= DAYS_IN_MONTH
        ):
            raise ProfileError("declared_funding_day is out of range")
        if self.typical_amount_p75 is not None:
            if not isinstance(self.typical_amount_p75, int):
                raise ProfileError("money is integer paise")
            if self.typical_amount_p75 < 0:
                raise ProfileError("typical_amount_p75 must be non-negative")
        if self.fatigue_score < 0:
            raise ProfileError("fatigue_score must be non-negative")

    # ── what the profile knows ─────────────────────────────────────────────

    @property
    def observations(self) -> float:
        """Effective number of observations behind the posterior.

        Because the counts are kept unnormalised, this is simply their sum —
        the evidence weight falls out of the representation instead of being
        reconstructed. Under a geometric decay it saturates near
        `1 / (1 - decay)`, about 33 cycles at §26's 0.97.

        That saturation is a feature: a customer observed for ten years is not
        thirty times more knowable than one observed for one, because the decay
        has already discarded the old evidence.
        """
        return float(sum(self.payday_posterior.values()))

    def normalised_posterior(self) -> dict[int, float]:
        """The posterior as a probability distribution (ADR-077's read step)."""
        total = self.observations
        if total <= 0:
            return {}
        return {day: mass / total for day, mass in self.payday_posterior.items()}

    @property
    def is_informative(self) -> bool:
        """§37's "at least three cycles" bar for trusting a profile at all."""
        return self.tenure_cycles >= MIN_CYCLES_FOR_PROFILE

    @property
    def payday_confidence(self) -> float:
        """How much the posterior can be trusted, in [0, 1].

        Two things multiplied, because either alone is misleading:

        *Concentration* — one minus normalised entropy. A posterior massed on
        one day scores near 1, a flat one near 0. Entropy rather than the modal
        probability, because a customer paid twice a month is *confidently*
        bimodal and a modal probability of 0.5 would understate what is known.

        *Support* — `n/(n+kappa)`, §21's shrinkage weight. **Concentration
        alone is not confidence.** A single observation is perfectly
        concentrated and says almost nothing, and reporting 1.0 for it would
        hand the sequencer a certainty that one cycle cannot buy — the
        overconfidence §21 warns about, arriving through the memory tier
        instead of the model.
        """
        n_eff = self.observations
        if n_eff <= 0:
            return 0.0

        entropy = 0.0
        for p in self.normalised_posterior().values():
            if p > 0:
                entropy -= p * math.log(p)

        concentration = max(0.0, 1.0 - entropy / math.log(DAYS_IN_MONTH))
        support = n_eff / (n_eff + DEFAULT_KAPPA)
        return float(concentration * support)

    def modal_payday(self) -> int | None:
        """The most likely day of month, or None if nothing has been observed."""
        if not self.payday_posterior:
            return None
        return max(self.payday_posterior, key=lambda d: self.payday_posterior[d])

    def declared_weight(self, now: datetime) -> float:
        """§26 — a declared hint's prior weight, decaying to zero over 180 days.

        Linear rather than exponential: §26 says "decays to zero over 180 days",
        and an exponential never reaches zero. A hint that is meant to expire
        should actually expire.
        """
        if self.declared_funding_day is None or self.declared_at is None:
            return 0.0
        age_days = (now - self.declared_at).total_seconds() / 86400.0
        if age_days < 0:
            raise ProfileError("declared_at is in the future")
        return float(max(0.0, 1.0 - age_days / DECLARED_TTL_DAYS))

    def payday_probability(self, day: int, now: datetime) -> float:
        """P(funding on this day of month), blending observation and hint.

        The declared hint enters as *prior weight on one day*, scaled by how
        fresh it is — never as an override. Invariant 9: LLM-derived data has a
        bounded effect and does not become an instruction.
        """
        if not 1 <= day <= DAYS_IN_MONTH:
            raise ProfileError(f"day-of-month {day} is out of range")

        observed = self.normalised_posterior().get(day, 0.0)

        weight = self.declared_weight(now)
        if weight <= 0:
            return float(observed)

        hinted = 1.0 if day == self.declared_funding_day else 0.0
        # The hint competes with the evidence rather than replacing it: with a
        # thin posterior it dominates, and it recedes as observations
        # accumulate. §21's shrinkage form, applied to a stated belief rather
        # than to a segment.
        n_eff = self.observations
        evidence = n_eff / (n_eff + DEFAULT_KAPPA)
        hint_share = (1.0 - evidence) * weight
        return float((1.0 - hint_share) * observed + hint_share * hinted)

    def current_fatigue(self, now: datetime) -> float:
        """§26's fatigue, decayed to `now`.

        Stored fatigue is only correct as of `last_contact_at`; reading it raw
        would treat a customer messaged a month ago as freshly fatigued. §24.6
        needs this to be able to choose silence.
        """
        if self.last_contact_at is None or self.fatigue_score <= 0:
            return 0.0
        age_days = (now - self.last_contact_at).total_seconds() / 86400.0
        if age_days <= 0:
            return float(self.fatigue_score)
        return float(self.fatigue_score * 0.5 ** (age_days / FATIGUE_HALF_LIFE_DAYS))


# ── update rules (§26) ─────────────────────────────────────────────────────


def update_payday(
    profile: CustomerPaymentProfile,
    success_day: int,
    *,
    at: datetime,
    weight: float = 1.0,
) -> CustomerPaymentProfile:
    """§26's Dirichlet update on an observed *successful* debit.

        for d in posterior: posterior[d] *= decay
        posterior[success_day] += weight

    **The normalise step §26 places here happens on read instead (ADR-077).**
    In place, it takes the accumulated count away: each observation would claim
    ~51% of the mass however long the history, one cycle would read as
    certainty, and the decay parameter would have no effect at all.

    Successful debits only. A failure says the account was empty at that moment,
    which is not evidence about when money arrives — folding failures in would
    teach the posterior the retry schedule instead of the payday.
    """
    if not 1 <= success_day <= DAYS_IN_MONTH:
        raise ProfileError(f"day-of-month {success_day} is out of range")
    if weight <= 0:
        raise ProfileError("weight must be positive")

    decayed = {day: mass * PAYDAY_DECAY for day, mass in profile.payday_posterior.items()}
    decayed[success_day] = decayed.get(success_day, 0.0) + weight

    return replace(
        profile,
        payday_posterior=decayed,
        tenure_cycles=profile.tenure_cycles + 1,
        consecutive_failures=0,
        updated_at=at,
    )


def record_failure(profile: CustomerPaymentProfile, *, at: datetime) -> CustomerPaymentProfile:
    """A failed cycle: risk state moves, the payday posterior does not."""
    return replace(
        profile,
        consecutive_failures=profile.consecutive_failures + 1,
        updated_at=at,
    )


def record_revocation(profile: CustomerPaymentProfile, *, at: datetime) -> CustomerPaymentProfile:
    return replace(
        profile,
        revocation_events=profile.revocation_events + 1,
        updated_at=at,
    )


def record_contact(
    profile: CustomerPaymentProfile, *, at: datetime, increment: float = 1.0
) -> CustomerPaymentProfile:
    """One message sent. Fatigue accrues on top of what has already decayed.

    Decaying first and then adding is what makes fatigue path-dependent in the
    way §26 intends: three messages in a week land far harder than three spread
    over three months, and the arithmetic has to reflect that rather than a
    running total that only ever grows.
    """
    if increment <= 0:
        raise ProfileError("increment must be positive")

    return replace(
        profile,
        fatigue_score=profile.current_fatigue(at) + increment,
        last_contact_at=at,
        messages_30d=profile.messages_30d + 1,
        updated_at=at,
    )


def record_declared_hint(
    profile: CustomerPaymentProfile, day: int, *, at: datetime
) -> CustomerPaymentProfile:
    """Accept a customer's stated funding day (§25, Invariant 9).

    Validated to a day of the month and stored with its timestamp. It shifts a
    prior and expires; it never reaches the money path as an instruction.
    """
    if not 1 <= day <= DAYS_IN_MONTH:
        raise ProfileError(f"declared day-of-month {day} is out of range")
    return replace(profile, declared_funding_day=day, declared_at=at, updated_at=at)


def record_settlement_lag(
    profile: CustomerPaymentProfile, lag_days: float, *, at: datetime, keep: int = 24
) -> CustomerPaymentProfile:
    """Empirical fail -> success lag, bounded to the most recent `keep`."""
    if lag_days < 0:
        raise ProfileError("lag_days must be non-negative")
    lags = (*profile.fail_success_lag_days, float(lag_days))[-keep:]
    return replace(profile, fail_success_lag_days=lags, updated_at=at)


def withdraw_consent(profile: CustomerPaymentProfile, *, at: datetime) -> CustomerPaymentProfile:
    """Mark consent withdrawn. **Erasure is `prayas.memory.forget`'s job.**

    Kept separate because the flag and the cascade are different operations
    with different failure modes: a flag that is set but whose cascade did not
    run is a profile still holding data it should not, and conflating them
    hides that state.
    """
    return replace(profile, consent_withdrawn=True, updated_at=at)


def decayed_to(profile: CustomerPaymentProfile, now: datetime) -> CustomerPaymentProfile:
    """The profile as it stands *now*, with time-dependent fields brought current.

    Used at read time so a caller reasoning about a stale record sees decayed
    fatigue rather than the value frozen at the last write.
    """
    return replace(
        profile,
        fatigue_score=profile.current_fatigue(now),
        last_contact_at=(profile.last_contact_at if profile.last_contact_at is not None else None),
    )


def empty_profile(tenant_id: str, customer_id: str) -> CustomerPaymentProfile:
    """A cold-start profile — knows nothing, and reports that honestly."""
    return CustomerPaymentProfile(tenant_id=tenant_id, customer_id=customer_id)
