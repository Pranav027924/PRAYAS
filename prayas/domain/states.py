"""State machines and legal transitions, transcribed from Master Spec §11.

Encoded as explicit transition tables rather than scattered `if` statements, so
that "is this transition legal?" has exactly one answer in exactly one place —
and so the property test asserting convergence has something concrete to check.

Terminal states absorb: once a cycle has succeeded, no later-arriving event may
move it. That property, plus a deterministic ordering, is what makes any
permutation of an event set converge to the same projected state (§40.3).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class MandateState(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    AT_RISK = "at_risk"
    REVOKED = "revoked"
    RE_ENROLLING = "re_enrolling"
    EXPIRED = "expired"


class CycleState(StrEnum):
    SCHEDULED = "scheduled"
    PDN_SENT = "pdn_sent"
    PDN_FAILED = "pdn_failed"
    DEFERRED = "deferred"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    BUDGET_EXHAUSTED = "budget_exhausted"
    STOPPED_ECONOMIC = "stopped_economic"
    STOPPED_HARD = "stopped_hard"
    DEFERRED_INTERVENTION = "deferred_intervention"
    SUPERSEDED = "superseded"


class AttemptState(StrEnum):
    PLANNED = "planned"
    GATED = "gated"
    FIRED = "fired"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    DENIED = "denied"
    AMBIGUOUS = "ambiguous"
    RECONCILED = "reconciled"


# ── MANDATE ──────────────────────────────────────────────────────────────────
#   created ──AFA──► active ──► paused ──► active
#                      │  │
#                      │  └──► at_risk ──► active
#                      │         │
#                      │         └──► revoked ──► re_enrolling ──► active
#                      └──────────────► expired
MANDATE_TRANSITIONS: Final[dict[MandateState, frozenset[MandateState]]] = {
    MandateState.CREATED: frozenset({MandateState.ACTIVE, MandateState.EXPIRED}),
    MandateState.ACTIVE: frozenset(
        {MandateState.PAUSED, MandateState.AT_RISK, MandateState.EXPIRED}
    ),
    MandateState.PAUSED: frozenset({MandateState.ACTIVE, MandateState.EXPIRED}),
    MandateState.AT_RISK: frozenset(
        {MandateState.ACTIVE, MandateState.REVOKED, MandateState.EXPIRED}
    ),
    MandateState.REVOKED: frozenset({MandateState.RE_ENROLLING}),
    MandateState.RE_ENROLLING: frozenset({MandateState.ACTIVE, MandateState.EXPIRED}),
    MandateState.EXPIRED: frozenset(),
}

# ── CYCLE ────────────────────────────────────────────────────────────────────
#   scheduled ─► pdn_sent ─► executing ─► succeeded
#                   │            ├─► budget_exhausted
#                   │            ├─► stopped_economic
#                   │            ├─► stopped_hard
#                   │            └─► deferred_intervention
#                   └─► pdn_failed ─► deferred
#       └─► superseded                     (paid out of band)
#
# `superseded` is reachable from any pre-terminal state: out-of-band payment is
# an external fact that can arrive at any point before the cycle settles.
_CYCLE_OUTCOMES: Final = frozenset(
    {
        CycleState.SUCCEEDED,
        CycleState.BUDGET_EXHAUSTED,
        CycleState.STOPPED_ECONOMIC,
        CycleState.STOPPED_HARD,
        CycleState.DEFERRED_INTERVENTION,
    }
)

CYCLE_TRANSITIONS: Final[dict[CycleState, frozenset[CycleState]]] = {
    CycleState.SCHEDULED: frozenset(
        {CycleState.PDN_SENT, CycleState.PDN_FAILED, CycleState.SUPERSEDED}
    ),
    CycleState.PDN_SENT: frozenset(
        {CycleState.EXECUTING, CycleState.PDN_FAILED, CycleState.SUPERSEDED}
    ),
    CycleState.PDN_FAILED: frozenset({CycleState.DEFERRED, CycleState.SUPERSEDED}),
    CycleState.DEFERRED: frozenset({CycleState.PDN_SENT, CycleState.SUPERSEDED}),
    CycleState.EXECUTING: frozenset(_CYCLE_OUTCOMES | {CycleState.SUPERSEDED}),
    CycleState.SUCCEEDED: frozenset(),
    CycleState.BUDGET_EXHAUSTED: frozenset(),
    CycleState.STOPPED_ECONOMIC: frozenset(),
    CycleState.STOPPED_HARD: frozenset(),
    CycleState.DEFERRED_INTERVENTION: frozenset(),
    CycleState.SUPERSEDED: frozenset(),
}

# ── ATTEMPT ──────────────────────────────────────────────────────────────────
#   planned ─► gated ─► fired ─► succeeded | failed
#      │         │        │
#      └─► cancelled   denied   ambiguous ─► reconciled
ATTEMPT_TRANSITIONS: Final[dict[AttemptState, frozenset[AttemptState]]] = {
    AttemptState.PLANNED: frozenset({AttemptState.GATED, AttemptState.CANCELLED}),
    AttemptState.GATED: frozenset(
        {AttemptState.FIRED, AttemptState.DENIED, AttemptState.CANCELLED}
    ),
    AttemptState.FIRED: frozenset(
        {AttemptState.SUCCEEDED, AttemptState.FAILED, AttemptState.AMBIGUOUS}
    ),
    AttemptState.AMBIGUOUS: frozenset({AttemptState.RECONCILED}),
    AttemptState.SUCCEEDED: frozenset(),
    AttemptState.FAILED: frozenset(),
    AttemptState.CANCELLED: frozenset(),
    AttemptState.DENIED: frozenset(),
    AttemptState.RECONCILED: frozenset(),
}

#: A cycle in one of these has settled; the late-capture guard cancels any
#: scheduled action still pending against it.
CYCLE_TERMINAL: Final[frozenset[CycleState]] = frozenset(
    state for state, onward in CYCLE_TRANSITIONS.items() if not onward
)

#: Settled *and paid* — the cases where money arrived.
CYCLE_PAID: Final[frozenset[CycleState]] = frozenset({CycleState.SUCCEEDED, CycleState.SUPERSEDED})

MANDATE_TERMINAL: Final[frozenset[MandateState]] = frozenset(
    state for state, onward in MANDATE_TRANSITIONS.items() if not onward
)

ATTEMPT_TERMINAL: Final[frozenset[AttemptState]] = frozenset(
    state for state, onward in ATTEMPT_TRANSITIONS.items() if not onward
)


def can_transition_cycle(current: CycleState, proposed: CycleState) -> bool:
    return proposed in CYCLE_TRANSITIONS[current]


def can_transition_mandate(current: MandateState, proposed: MandateState) -> bool:
    return proposed in MANDATE_TRANSITIONS[current]


def can_transition_attempt(current: AttemptState, proposed: AttemptState) -> bool:
    return proposed in ATTEMPT_TRANSITIONS[current]
