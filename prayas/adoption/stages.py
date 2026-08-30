"""The adoption ramp, as code rather than a document (Master Spec §44; ADR-089).

§44 opens with the reason this exists: "No internal platform team adopts
anything without this. **It is a product feature, not a rollout plan.**"

Phase 17's exit criterion is the same point put sharply — "stage transitions
gated by their exit criteria **in code, not in a document**." A ramp whose
gates live in a wiki is a ramp somebody advances on a Friday because the
meeting went well. So every criterion in §44's table is a predicate here, and
`advance` refuses without the evidence.

**Behaviour is per stage and is enforced, not described.** Observe ingests and
decides nothing; Shadow decides and fires nothing; Canary fires for 1% with no
holdout; Ramp runs 10% with a 15% holdout inside it; Full is portfolio-wide.
The executor consults this before firing, next to the kill switch (ADR-084), so
"fire nothing" is a property of the money path rather than a promise about
configuration.

**The holdout is never retired.** §44: "It is how the system continues to know
it is working, and it is what makes any value claim renewable rather than a
one-time measurement." `FULL` therefore keeps a holdout, and there is no stage
whose holdout is zero after Canary — which is why `holdout_pct` is a property
of the stage and not a separate setting someone can turn off.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Final


class Stage(IntEnum):
    """§44's five stages. Ordered, because a ramp only goes one way by default."""

    OBSERVE = 0
    SHADOW = 1
    CANARY = 2
    RAMP = 3
    FULL = 4


class AdoptionError(RuntimeError):
    """A stage transition that §44 does not permit."""


@dataclass(frozen=True, slots=True)
class Behaviour:
    """What a stage *does*, as §44's "Behaviour" column."""

    decides: bool
    fires: bool
    mandate_share: float
    holdout_pct: float
    description: str


#: §44's behaviour column, transcribed.
BEHAVIOUR: Final[dict[Stage, Behaviour]] = {
    Stage.OBSERVE: Behaviour(
        decides=False,
        fires=False,
        mandate_share=0.0,
        holdout_pct=0.0,
        description="Ingest only. No decisions.",
    ),
    Stage.SHADOW: Behaviour(
        decides=True,
        fires=False,
        mandate_share=0.0,
        holdout_pct=0.0,
        description="Decide and log. Fire nothing.",
    ),
    Stage.CANARY: Behaviour(
        decides=True,
        fires=True,
        mandate_share=0.01,
        # §44: "treatment only, no holdout". A 1% canary split further would
        # give a control arm too thin to conclude anything from, and the point
        # of this stage is safety rather than measurement.
        holdout_pct=0.0,
        description="1% of mandates, treatment only, no holdout.",
    ),
    Stage.RAMP: Behaviour(
        decides=True,
        fires=True,
        mandate_share=0.10,
        holdout_pct=0.15,
        description="10% of portfolio, 15% holdout inside it.",
    ),
    Stage.FULL: Behaviour(
        decides=True,
        fires=True,
        mandate_share=1.0,
        # Never zero. See the module docstring.
        holdout_pct=0.15,
        description="Portfolio-wide, holdout maintained permanently.",
    ),
}


@dataclass(frozen=True, slots=True)
class Evidence:
    """What is known about a tenant's readiness to advance.

    Every field maps to a cell in §44's "Exit criteria" column. Defaults are
    the *unproven* value in each case — zero days, zero decisions, unmeasured
    calibration — so evidence that was never gathered cannot be mistaken for
    evidence that passed.
    """

    # Stage 0 -> 1
    event_completeness: float = 0.0
    projection_matches_days: int = 0
    # Stage 1 -> 2
    shadow_decisions: int = 0
    gate_errors: int = 0
    calibration_ece: float = 1.0
    audit_report_produced: bool = False
    # Stage 2 -> 3
    canary_days: int = 0
    double_debits: int = 0
    compliance_violations: int = 0
    revocation_rate_above_baseline: bool = True
    # Stage 3 -> 4
    recovery_ci_excludes_zero: bool = False
    survival_ci_negative: bool = True
    guardrails_green_days: int = 0


@dataclass(frozen=True, slots=True)
class GateResult:
    """Whether a transition is permitted, and precisely what is missing."""

    permitted: bool
    unmet: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.permitted


def _observe_to_shadow(e: Evidence) -> list[str]:
    """§44: "Event completeness >= 99.9% vs provider reconciliation; state
    projection matches provider truth for 7 days"."""
    unmet: list[str] = []
    if e.event_completeness < 0.999:
        unmet.append(f"event completeness {e.event_completeness:.4f} below 0.999")
    if e.projection_matches_days < 7:
        unmet.append(f"projection matched for {e.projection_matches_days} days, needs 7")
    return unmet


def _shadow_to_canary(e: Evidence) -> list[str]:
    """§44: ">= 10,000 shadow decisions; zero gate errors; calibration ECE
    < 0.05; §8 audit report produced"."""
    unmet: list[str] = []
    if e.shadow_decisions < 10_000:
        unmet.append(f"{e.shadow_decisions} shadow decisions, needs 10,000")
    if e.gate_errors != 0:
        unmet.append(f"{e.gate_errors} gate errors, needs zero")
    if not e.calibration_ece < 0.05:
        unmet.append(f"calibration ECE {e.calibration_ece:.4f} not below 0.05")
    if not e.audit_report_produced:
        unmet.append("§8 audit report not produced")
    return unmet


def _canary_to_ramp(e: Evidence) -> list[str]:
    """§44: "14 days; zero double debits; zero violations; revocation rate not
    above baseline"."""
    unmet: list[str] = []
    if e.canary_days < 14:
        unmet.append(f"{e.canary_days} canary days, needs 14")
    # §42 gives these two no error budget: "any occurrence is a Sev-1".
    if e.double_debits != 0:
        unmet.append(f"{e.double_debits} double debits, needs zero")
    if e.compliance_violations != 0:
        unmet.append(f"{e.compliance_violations} compliance violations, needs zero")
    if e.revocation_rate_above_baseline:
        unmet.append("revocation rate above baseline")
    return unmet


def _ramp_to_full(e: Evidence) -> list[str]:
    """§44: "Incremental recovery CI excludes zero; survival CI not negative;
    guardrails green 30 days"."""
    unmet: list[str] = []
    if not e.recovery_ci_excludes_zero:
        unmet.append("incremental recovery CI includes zero")
    if e.survival_ci_negative:
        unmet.append("survival CI is negative")
    if e.guardrails_green_days < 30:
        unmet.append(f"guardrails green for {e.guardrails_green_days} days, needs 30")
    return unmet


#: One predicate per transition, returning the unmet criteria. A stage absent
#: from this map cannot be advanced *from*, which is why `FULL` has no entry —
#: §44 gives it no exit criteria because there is nowhere further to go.
Gate = Callable[[Evidence], list[str]]

GATES: Final[dict[Stage, Gate]] = {
    Stage.OBSERVE: _observe_to_shadow,
    Stage.SHADOW: _shadow_to_canary,
    Stage.CANARY: _canary_to_ramp,
    Stage.RAMP: _ramp_to_full,
}


def can_advance(current: Stage, evidence: Evidence) -> GateResult:
    """Whether `current` may advance one stage, and what is missing if not.

    Returns the unmet criteria rather than a bare False. "Not yet" without a
    reason is how a gate becomes something people route around.
    """
    if current is Stage.FULL:
        return GateResult(permitted=False, unmet=("already at the final stage",))

    unmet = tuple(GATES[current](evidence))
    return GateResult(permitted=not unmet, unmet=unmet)


def advance(current: Stage, evidence: Evidence) -> Stage:
    """Move one stage forward, or refuse with the reasons.

    **One stage at a time.** §44's stages are sequential because each one's
    exit criteria are evidence gathered *during* the stage before it: you
    cannot demonstrate 14 days of clean canary without having run a canary.
    Skipping is not a faster ramp, it is an unevidenced one.
    """
    result = can_advance(current, evidence)
    if not result:
        raise AdoptionError(f"cannot advance from {current.name}: " + "; ".join(result.unmet))
    return Stage(current + 1)


def behaviour(stage: Stage) -> Behaviour:
    return BEHAVIOUR[stage]


def may_fire(stage: Stage) -> bool:
    """Whether *any* action may fire at this stage (§44's behaviour column)."""
    return BEHAVIOUR[stage].fires


def may_decide(stage: Stage) -> bool:
    return BEHAVIOUR[stage].decides
