"""Cause inference, V0 (Master Spec §20).

Maps `(decline code, context) -> posterior over true cause`.

**The deterministic layer does most of the work and needs no model.** §20:
"Codes 41, 43, 14, 54 and explicit mandate revocation resolve with certainty to
a terminal cause. No model needed, no model wanted."

**Code 05 is the interesting case** — §20: "30-40% of all declines and the least
informative signal in payments, with roughly half being insufficient funds in
disguise. The signal is entirely contextual."

V0 uses a transparent contextual heuristic over §20's feature table, deliberately
*not* a model. The EM-over-latent-variable version is Phase 11. Keeping V0 a
readable set of weighted signals means its confusion matrix against simulator
ground truth is interpretable, which is the point of having ground truth at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from prayas.sim.config import (
    CREDENTIAL_DEAD,
    FRAUD_HOLD,
    ISSUER_DEGRADED,
    LIMIT_BREACH,
    MANDATE_DEAD,
    NO_FUNDS,
)

#: §20 — terminal resolutions. No model wanted.
DETERMINISTIC: Final[dict[str, str]] = {
    "41": CREDENTIAL_DEAD,  # lost card
    "43": CREDENTIAL_DEAD,  # stolen card
    "14": CREDENTIAL_DEAD,  # invalid account number
    "54": CREDENTIAL_DEAD,  # expired card
    "57": MANDATE_DEAD,  # transaction not permitted
}

#: §20 — 51 is "near-deterministic" no_funds: the issuer said so plainly.
NEAR_DETERMINISTIC: Final[dict[str, tuple[str, float]]] = {
    "51": (NO_FUNDS, 0.97),
    "91": (ISSUER_DEGRADED, 0.90),
    "61": (LIMIT_BREACH, 0.90),
}

DO_NOT_HONOR: Final = "05"

#: Below this the issuer is treated as unhealthy (§20's `issuer_wilson_lower`).
ISSUER_UNHEALTHY_BELOW: Final = 0.85

#: Amount this many times the customer's p75 suggests a limit breach (§20).
LIMIT_BREACH_RATIO: Final = 2.5


@dataclass(frozen=True, slots=True)
class CauseContext:
    """§20's discriminating features. Observable only — never ground truth."""

    #: Wilson lower bound on issuer success at failure time.
    issuer_wilson_lower: float = 1.0
    #: Other customers, same issuer, same 5-minute window.
    peer_success_rate: float = 1.0
    #: This customer's other mandates, same window.
    sibling_failure_rate: float = 0.0
    #: Amount divided by p75 of the customer's successful debits.
    amount_ratio: float = 1.0
    #: Inferred funding cycle from profile memory.
    days_since_inferred_payday: int | None = None
    #: Competition for the same rupees.
    n_concurrent_mandates: int = 1


@dataclass(frozen=True, slots=True)
class CausePosterior:
    """A distribution over §20's causes, plus how it was reached."""

    posterior: dict[str, float]
    deterministic: bool

    @property
    def most_likely(self) -> str:
        return max(self.posterior, key=lambda cause: self.posterior[cause])

    @property
    def confidence(self) -> float:
        return self.posterior[self.most_likely]


def _normalise(weights: dict[str, float]) -> dict[str, float]:
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("cause weights must sum to something positive")
    return {cause: weight / total for cause, weight in weights.items()}


def infer(decline_code: str | None, ctx: CauseContext | None = None) -> CausePosterior:
    """Posterior over the latent cause.

    A missing or unrecognised code is not treated as `no_funds`: guessing the
    most common cause would make the confusion matrix flatter than the evidence
    warrants. It resolves to a spread posterior instead.
    """
    ctx = ctx or CauseContext()

    if decline_code in DETERMINISTIC:
        return CausePosterior({DETERMINISTIC[decline_code]: 1.0}, deterministic=True)

    if decline_code in NEAR_DETERMINISTIC:
        cause, confidence = NEAR_DETERMINISTIC[decline_code]
        remainder = 1.0 - confidence
        return CausePosterior(
            _normalise(
                {cause: confidence, NO_FUNDS: remainder * 0.6, ISSUER_DEGRADED: remainder * 0.4}
            )
            if cause != NO_FUNDS
            else _normalise(
                {
                    NO_FUNDS: confidence,
                    ISSUER_DEGRADED: remainder * 0.5,
                    LIMIT_BREACH: remainder * 0.5,
                }
            ),
            deterministic=False,
        )

    if decline_code == DO_NOT_HONOR:
        return _infer_do_not_honor(ctx)

    # Unknown code: spread rather than guess.
    return CausePosterior(
        _normalise({NO_FUNDS: 0.35, ISSUER_DEGRADED: 0.25, FRAUD_HOLD: 0.20, LIMIT_BREACH: 0.20}),
        deterministic=False,
    )


def _infer_do_not_honor(ctx: CauseContext) -> CausePosterior:
    """§20's contextual heuristic for code 05. Weights, not a fitted model.

    Each signal is one term so a wrong answer can be traced to the signal that
    caused it — which is what makes the confusion matrix actionable rather than
    merely a number.
    """
    weights: dict[str, float] = {
        NO_FUNDS: 1.0,
        ISSUER_DEGRADED: 0.35,
        FRAUD_HOLD: 0.25,
        LIMIT_BREACH: 0.20,
    }

    # Issuer-side evidence: a degraded issuer fails everyone, so peers failing
    # too rules the customer's balance out (§20).
    if ctx.issuer_wilson_lower < ISSUER_UNHEALTHY_BELOW:
        weights[ISSUER_DEGRADED] *= 4.0
        weights[NO_FUNDS] *= 0.5
    if ctx.peer_success_rate < 0.5:
        weights[ISSUER_DEGRADED] *= 3.0
        weights[NO_FUNDS] *= 0.6

    # Customer-side evidence: this customer's other mandates failing in the same
    # window points at the account, not the issuer.
    if ctx.sibling_failure_rate > 0.5:
        weights[NO_FUNDS] *= 2.5
        weights[ISSUER_DEGRADED] *= 0.5

    # A debit far above the customer's norm looks like a ceiling, not an
    # empty account.
    if ctx.amount_ratio >= LIMIT_BREACH_RATIO:
        weights[LIMIT_BREACH] *= 4.0
    elif ctx.amount_ratio > 1.4:
        weights[LIMIT_BREACH] *= 2.0

    # Distance from payday is the strongest no_funds signal available (§20).
    if ctx.days_since_inferred_payday is not None:
        if ctx.days_since_inferred_payday >= 20:
            weights[NO_FUNDS] *= 2.5
        elif ctx.days_since_inferred_payday <= 2:
            weights[NO_FUNDS] *= 0.5

    # Competition for the same rupees (§20).
    if ctx.n_concurrent_mandates >= 4:
        weights[NO_FUNDS] *= 1.6

    return CausePosterior(_normalise(weights), deterministic=False)


def is_terminal(cause: str) -> bool:
    """§20 — causes that are not recoverable. The right action is to stop."""
    return cause in {MANDATE_DEAD, CREDENTIAL_DEAD}
