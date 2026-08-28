"""Simulator configuration (Master Spec §38).

"Ground truth is the entire point: it is what allows cause inference to be
validated with a real confusion matrix."

Every parameter is published (§38's robustness suite depends on being able to
perturb them in front of a reviewer), and the config is frozen so a run cannot
drift halfway through and invalidate its own labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

#: §38 payday archetypes.
SALARIED_1ST: Final = "salaried_1st"
SALARIED_7TH: Final = "salaried_7th"
GIG_IRREGULAR: Final = "gig_irregular"
CHRONICALLY_DRY: Final = "chronically_dry"

PAYDAY_ARCHETYPES: Final[tuple[str, ...]] = (
    SALARIED_1ST,
    SALARIED_7TH,
    GIG_IRREGULAR,
    CHRONICALLY_DRY,
)

#: §9 rails.
RAILS: Final[tuple[str, ...]] = ("upi_autopay", "card_emandate", "enach")

#: §20 latent causes.
NO_FUNDS: Final = "no_funds"
ISSUER_DEGRADED: Final = "issuer_degraded"
FRAUD_HOLD: Final = "fraud_hold"
LIMIT_BREACH: Final = "limit_breach"
MANDATE_DEAD: Final = "mandate_dead"
CREDENTIAL_DEAD: Final = "credential_dead"

CAUSES: Final[tuple[str, ...]] = (
    NO_FUNDS,
    ISSUER_DEGRADED,
    FRAUD_HOLD,
    LIMIT_BREACH,
    MANDATE_DEAD,
    CREDENTIAL_DEAD,
)

#: §20 — "Codes 41, 43, 14, 54 and explicit mandate revocation resolve with
#: certainty to a terminal cause. No model needed, no model wanted."
DETERMINISTIC_CODES: Final[dict[str, str]] = {
    "41": CREDENTIAL_DEAD,  # lost card
    "43": CREDENTIAL_DEAD,  # stolen card
    "14": CREDENTIAL_DEAD,  # invalid account number
    "54": CREDENTIAL_DEAD,  # expired card
    "57": MANDATE_DEAD,  # transaction not permitted
    "51": NO_FUNDS,  # insufficient funds, stated plainly
}

#: §20 — "code 05, 'Do Not Honor' — 30-40% of all declines and the least
#: informative signal in payments, with roughly half being insufficient funds
#: in disguise."
DO_NOT_HONOR: Final = "05"


class ConfigError(ValueError):
    """The configuration is not a valid generative process."""


def _check_mix(name: str, mix: dict[str, float], allowed: tuple[str, ...]) -> None:
    unknown = set(mix) - set(allowed)
    if unknown:
        raise ConfigError(f"{name} has unknown keys: {sorted(unknown)}")
    if not mix:
        raise ConfigError(f"{name} is empty")
    if any(weight < 0 for weight in mix.values()):
        raise ConfigError(f"{name} has a negative weight")
    total = sum(mix.values())
    if abs(total - 1.0) > 1e-9:
        raise ConfigError(f"{name} must sum to 1.0, got {total}")


@dataclass(frozen=True, slots=True)
class SimConfig:
    """§38's parameter set, validated.

    Defaults describe a plausible Indian recurring-debit population. They are
    starting points to perturb, not claims about reality — §38's whole argument
    is that the parameters are published so a reviewer can move them.
    """

    #: Mixture over payday archetypes.
    payday_mix: dict[str, float] = field(
        default_factory=lambda: {
            SALARIED_1ST: 0.45,
            SALARIED_7TH: 0.20,
            GIG_IRREGULAR: 0.25,
            CHRONICALLY_DRY: 0.10,
        }
    )
    #: Poisson onset rate per issuer-day.
    outage_lambda: float = 0.05
    #: LogNormal(mu, sigma) outage duration in minutes.
    outage_duration: tuple[float, float] = (4.0, 0.8)
    #: P(surface as "05" | true cause = no_funds). §38 suggests ~0.5.
    mask_05_rate: float = 0.5
    #: Revocation hazard added per consecutive failure.
    revocation_beta: float = 0.04
    #: Nudge efficacy decay per message.
    fatigue_decay: float = 0.25
    #: Share of mandates per rail.
    rail_mix: dict[str, float] = field(
        default_factory=lambda: {"upi_autopay": 0.70, "card_emandate": 0.20, "enach": 0.10}
    )
    #: §38 — "money arrives AND is spent — tests §21's assumption".
    #: §21 assumes funding is absorbing: once funded, it stays funded. When True
    #: the account can be drained again, which is the stated limitation measured.
    non_absorbing: bool = False

    #: Cycle amount distribution, integer paise. Money is never float.
    amount_paise_range: tuple[int, int] = (9_900, 499_900)
    #: Horizon in days over which a cycle may recover.
    horizon_days: int = 30
    #: Baseline probability a debit fails for a reason unrelated to funding.
    base_failure_rate: float = 0.18

    #: ADR-058 — mandates a single customer holds. A *recurring*-debit
    #: simulator in which no customer recurs cannot express §24.2's attention
    #: patterns, ADR-049's CUPED pre-period, or §12's memory at all. §33 already
    #: models this: "one customer may hold mandates with several merchants".
    cycles_per_customer: int = 4

    #: ADR-059 — P(customer tops up in time) for a perfectly-timed notice.
    #: §24.2: "One sent at the 24-hour boundary, the evening before a salary
    #: credit lands, is acted on."
    pdn_uplift_max: float = 0.35

    #: How fast that effect decays as the notice moves away from the eve of
    #: predicted funding. §24.2: "A notice sent 72 hours early is forgotten."
    pdn_attention_decay_hours: float = 18.0

    def __post_init__(self) -> None:
        _check_mix("payday_mix", self.payday_mix, PAYDAY_ARCHETYPES)
        _check_mix("rail_mix", self.rail_mix, RAILS)

        if self.outage_lambda < 0:
            raise ConfigError("outage_lambda must be non-negative")
        if self.outage_duration[1] <= 0:
            raise ConfigError("outage_duration sigma must be positive")
        if not 0.0 <= self.mask_05_rate <= 1.0:
            raise ConfigError("mask_05_rate must be a probability")
        if not 0.0 <= self.fatigue_decay <= 1.0:
            raise ConfigError("fatigue_decay must be in [0, 1]")
        if self.revocation_beta < 0:
            raise ConfigError("revocation_beta must be non-negative")
        if not 0.0 <= self.base_failure_rate <= 1.0:
            raise ConfigError("base_failure_rate must be a probability")
        if self.cycles_per_customer < 1:
            raise ConfigError("cycles_per_customer must be at least 1")
        if not 0.0 <= self.pdn_uplift_max <= 1.0:
            raise ConfigError("pdn_uplift_max must be a probability")
        if self.pdn_attention_decay_hours <= 0:
            raise ConfigError("pdn_attention_decay_hours must be positive")
        if self.horizon_days <= 0:
            raise ConfigError("horizon_days must be positive")

        low, high = self.amount_paise_range
        if low <= 0 or high < low:
            raise ConfigError("amount_paise_range must be positive and ordered")
