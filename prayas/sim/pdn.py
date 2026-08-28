"""Notification response, as a function of *when* the notice lands (ADR-059).

§24.2 states the mechanism plainly:

    "A notice sent 72 hours early is forgotten. One sent at the 24-hour
    boundary, the evening before a salary credit lands, is acted on. The gap
    between those two outcomes is free."

So the response cannot be a constant. A fixed uplift would make timing
irrelevant by construction and reproduce FINDING-P8-01 exactly — a real number
attached to a false mechanism claim. Here the effect peaks when the notice
lands on the eve of predicted funding and decays away from it, which is what
makes "optimised timing beats naive timing" a falsifiable claim rather than an
assumption baked into the generator.

Both parameters are published in `SimConfig` and swept by the robustness suite,
so the result cannot rest on a flattering constant.

**This is ground truth.** Nothing here may be read by a policy deciding when to
send; it exists to *score* a decision after the fact (ADR-030).
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime

from prayas.sim.config import SimConfig


def attention_quality(
    send_at: datetime, funding_at: datetime | None, *, decay_hours: float
) -> float:
    """How well-timed a notice was, in [0, 1].

    Peaks at 1.0 for a notice landing on the eve of funding, and decays with
    the gap in either direction — a notice *after* the money has arrived and
    been spent is as useless as one sent days early.

    Returns 0.0 when funding never arrives: there is nothing to be reminded
    about, which is §1's "spend zero when it never will" population.
    """
    if funding_at is None:
        return 0.0
    if decay_hours <= 0:
        raise ValueError(f"decay_hours must be positive, got {decay_hours}")

    gap_hours = abs((funding_at - send_at).total_seconds()) / 3600.0
    return math.exp(-gap_hours / decay_hours)


def prevention_probability(
    config: SimConfig,
    *,
    send_at: datetime | None,
    funding_at: datetime | None,
    messages_already_sent: int = 0,
) -> float:
    """P(the customer tops up in time because of this notice).

    Scaled by `fatigue_decay`: §24.6 warns that "a system that cannot select
    silence will over-message its way through its own portfolio", so each prior
    message makes the next one less effective, not more.
    """
    if send_at is None:
        return 0.0

    quality = attention_quality(send_at, funding_at, decay_hours=config.pdn_attention_decay_hours)
    fatigue = (1.0 - config.fatigue_decay) ** max(messages_already_sent, 0)
    return config.pdn_uplift_max * quality * fatigue


def responds(
    config: SimConfig,
    *,
    cycle_id: str,
    send_at: datetime | None,
    funding_at: datetime | None,
    messages_already_sent: int = 0,
) -> bool:
    """Whether this customer actually acted on the notice.

    Deterministic in `cycle_id`, so a run is reproducible and two policies
    differing only in send time are compared against the *same* customer
    disposition rather than against independent coin flips. Without that, the
    measured difference would include sampling noise the policies did not cause.
    """
    probability = prevention_probability(
        config,
        send_at=send_at,
        funding_at=funding_at,
        messages_already_sent=messages_already_sent,
    )
    if probability <= 0.0:
        return False

    digest = hashlib.sha256(f"pdn:{cycle_id}".encode()).digest()
    roll = int.from_bytes(digest[:8], "big") / float(1 << 64)
    return roll < probability
