"""Which mandates a stage treats, and which are held out (ADR-095; §44).

`BEHAVIOUR` gives every stage a `mandate_share` and a `holdout_pct` — CANARY
treats 1%, RAMP 10% with a 15% holdout inside it, FULL the portfolio with the
holdout kept permanently. Nothing read either field. A tenant advanced to
CANARY would therefore have treated **everything**, which is the one thing a
canary exists to prevent, and FULL would have had no control arm, which is what
Phase 18's "measured against their own holdout" depends on.

**Assignment is a hash, not a coin flip, and that is the whole design.** A
mandate must land in the same arm on every evaluation, forever: if membership
were random per call, a mandate would drift between treatment and control, and
the comparison at the end would be between two populations that never existed.
Hashing `(tenant_id, mandate_id, salt)` gives a stable, uniform, restart-proof
assignment that needs no stored roster and no coordination between replicas.

**Two independent draws, not one threshold.** Selection into the treated share
and selection into the holdout use different salts. Deriving both from one hash
would correlate them — the holdout would always be drawn from the same end of
the share's ordering, so the control arm would be a biased slice of the
portfolio rather than a random one.

The holdout is a fraction *of the treated share*, matching §44's "10% of
portfolio, 15% holdout inside it".
"""

from __future__ import annotations

from enum import StrEnum
from hashlib import blake2b
from typing import Final

from prayas.adoption.stages import BEHAVIOUR, Stage

#: Distinct salts keep the two draws independent. Changing either reshuffles
#: every assignment, which invalidates an in-flight comparison — so they are
#: constants, never configuration.
_SHARE_SALT: Final = b"prayas.adoption.share.v1"
_HOLDOUT_SALT: Final = b"prayas.adoption.holdout.v1"


class Arm(StrEnum):
    """Which arm a mandate is in for this stage."""

    #: Outside the stage's rollout. No decisions, no actions.
    EXCLUDED = "excluded"
    #: Inside the rollout, deliberately left alone so it can be compared against.
    HOLDOUT = "holdout"
    #: Inside the rollout and acted on.
    TREATMENT = "treatment"


def _unit_interval(*, tenant_id: str, mandate_id: str, salt: bytes) -> float:
    """A stable uniform draw in [0, 1) for this mandate under this salt.

    Tenant-scoped so one tenant's assignment tells you nothing about another's,
    and so two tenants with a same-named mandate do not share an arm.
    """
    digest = blake2b(f"{tenant_id}\x00{mandate_id}".encode(), key=salt[:64], digest_size=8).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def arm_for(stage: Stage, *, tenant_id: str, mandate_id: str) -> Arm:
    """The arm this mandate is in at this stage.

    Deterministic in `(stage, tenant_id, mandate_id)`. Advancing a stage widens
    the treated share, and because the draw is stable a mandate already in
    treatment stays in treatment — a ramp adds mandates rather than reshuffling
    them, which is what makes the before/after comparable.
    """
    behaviour = BEHAVIOUR[stage]

    if behaviour.mandate_share <= 0.0:
        return Arm.EXCLUDED

    share_draw = _unit_interval(tenant_id=tenant_id, mandate_id=mandate_id, salt=_SHARE_SALT)
    if share_draw >= behaviour.mandate_share:
        return Arm.EXCLUDED

    if behaviour.holdout_pct <= 0.0:
        return Arm.TREATMENT

    holdout_draw = _unit_interval(tenant_id=tenant_id, mandate_id=mandate_id, salt=_HOLDOUT_SALT)
    return Arm.HOLDOUT if holdout_draw < behaviour.holdout_pct else Arm.TREATMENT


def may_act_on(stage: Stage, *, tenant_id: str, mandate_id: str) -> bool:
    """Whether this stage may schedule an action for this mandate."""
    return arm_for(stage, tenant_id=tenant_id, mandate_id=mandate_id) is Arm.TREATMENT
