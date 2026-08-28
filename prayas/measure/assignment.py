"""Deterministic arm assignment (Master Spec §33).

"Deterministic assignment from a committed seed. No assignment table, no
leakage, fully reproducible."

Two properties do the work:

**Customer-level, not cycle-level.** §33: "One customer may hold mandates with
several merchants; randomising per cycle leaks treatment across arms within a
customer." A customer who is treated on one cycle and control on the next
contaminates both arms, and the contamination is invisible in the totals.

**No assignment table.** The arm is a pure function of `(seed, customer_id)`,
so it can be recomputed at any time, cannot drift, and cannot be edited after
the fact to flatter a result. The seed is frozen in `experiment_config` with a
git commit hash before the run (§33), which is what makes the pre-registration
claim checkable.
"""

from __future__ import annotations

import hashlib
from typing import Final

CONTROL: Final = "control"
TREATMENT: Final = "treatment"

#: §33 hashes into 4 hex chars and compares against control_pct * 10_000.
_BUCKETS: Final = 10_000
_HEX_CHARS: Final = 8


class AssignmentError(ValueError):
    """The experiment is not well formed."""


def arm(customer_id: str, seed: str, control_pct: float) -> str:
    """§33's assignment, transcribed.

        h = sha256(f"{seed}:{customer_id}").hexdigest()
        return "control" if (int(h[:8], 16) % 10_000) < control_pct * 10_000
               else "treatment"

    Keyed on `customer_id`, never on cycle or mandate.
    """
    if not 0.0 <= control_pct <= 1.0:
        raise AssignmentError(f"control_pct must be a probability, got {control_pct}")
    if not customer_id:
        raise AssignmentError("customer_id must be non-empty")
    if not seed:
        raise AssignmentError("seed must be non-empty - an unseeded run is unreproducible")

    digest = hashlib.sha256(f"{seed}:{customer_id}".encode()).hexdigest()
    bucket = int(digest[:_HEX_CHARS], 16) % _BUCKETS
    return CONTROL if bucket < control_pct * _BUCKETS else TREATMENT


def propensity(assigned_arm: str, control_pct: float) -> float:
    """P(the unit received the arm it received) — ADR-048.

    This is what `decisions.propensity` records. The Phase 5 sequencer is
    deterministic, so the chosen *action* has propensity 1.0 and action-level
    IPS degenerates; the genuine randomisation in this system is the arm
    assignment above, so that is the probability worth logging.

    §34's IPS and doubly-robust estimators are therefore valid at the arm
    level, which is what the A/B comparison needs. Action-level off-policy
    evaluation requires an exploring policy and is deliberately not available.
    """
    if not 0.0 <= control_pct <= 1.0:
        raise AssignmentError(f"control_pct must be a probability, got {control_pct}")
    if assigned_arm == CONTROL:
        return control_pct
    if assigned_arm == TREATMENT:
        return 1.0 - control_pct
    raise AssignmentError(f"unknown arm {assigned_arm!r}")


def assign_many(customer_ids: list[str], seed: str, control_pct: float) -> dict[str, str]:
    """Assign a population. Order-independent, since each unit is hashed alone."""
    return {cid: arm(cid, seed, control_pct) for cid in customer_ids}
