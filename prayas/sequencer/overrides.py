"""Hard stops (Master Spec §23.4).

"Compliance and terminal-cause stops sit outside the economics and are
evaluated first. **An economic argument must never be able to override a legal
one.**"

That ordering is structural here, not conventional: `decide_stop` returns a
hard stop before the DP is consulted at all, and the DP has no access to these
predicates. There is no code path in which a favourable expected value can
overturn one.

These are *not* the compliance gate. §30's gate re-evaluates every rule at fire
time and is the only thing authorised to permit a debit (Invariant 1). These
overrides stop the sequencer from proposing an action that is already known to
be unlawful or pointless — cheaper, and a clearer audit trail, but never a
substitute.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

#: §20 — causes that resolve with certainty to a terminal state. No retry
#: schedule recovers a dead credential or a revoked mandate.
TERMINAL_CAUSES: Final[frozenset[str]] = frozenset({"credential_dead", "mandate_dead"})


@dataclass(frozen=True, slots=True)
class CycleContext:
    """Everything the hard stops need. Deliberately plain data.

    Assembled by the caller from projected state, so the predicates cannot
    reach into the database and quietly acquire a dependency on live state at a
    moment when §17.4 requires a value read inside the firing transaction.
    """

    cause: str
    mandate_state: str
    customer_opted_out: bool
    attempts_used: int
    attempt_budget: int
    deadline_at: datetime
    now: datetime


@dataclass(frozen=True, slots=True)
class HardStop:
    """A stop that economics cannot overturn."""

    reason: str
    detail: str


#: §23.4's HARD_STOPS, each paired with the message it produces. Order is the
#: spec's; every predicate is evaluated so the ledger records all that applied,
#: mirroring §30.2's "evaluate ALL — never short-circuit".
_PREDICATES: Final[tuple[tuple[str, Callable[[CycleContext], bool], str], ...]] = (
    (
        "terminal_cause",
        lambda c: c.cause in TERMINAL_CAUSES,
        "cause is terminal; no attempt schedule can recover it",
    ),
    (
        "mandate_not_active",
        lambda c: c.mandate_state != "active",
        "mandate is not active",
    ),
    (
        "customer_opted_out",
        lambda c: c.customer_opted_out,
        "customer has opted out",
    ),
    (
        "budget_exhausted",
        lambda c: c.attempts_used >= c.attempt_budget,
        "the regulator's attempt budget is spent",
    ),
    (
        "past_deadline",
        lambda c: c.now > c.deadline_at,
        "the cycle deadline has passed",
    ),
)


def hard_stops(ctx: CycleContext) -> list[HardStop]:
    """Every hard stop that applies, not merely the first.

    A reviewer asking "was the budget check performed?" needs a positive
    answer regardless of what else fired first — the same reasoning §30.2
    gives for not short-circuiting the gate.
    """
    return [
        HardStop(reason=reason, detail=_detail(ctx, reason, template))
        for reason, predicate, template in _PREDICATES
        if predicate(ctx)
    ]


def _detail(ctx: CycleContext, reason: str, template: str) -> str:
    if reason == "terminal_cause":
        return f"{template} (cause: {ctx.cause})"
    if reason == "mandate_not_active":
        return f"{template} (state: {ctx.mandate_state})"
    if reason == "budget_exhausted":
        return f"{template} ({ctx.attempts_used} of {ctx.attempt_budget} used)"
    if reason == "past_deadline":
        return f"{template} (deadline {ctx.deadline_at.isoformat()})"
    return template


def is_hard_stopped(ctx: CycleContext) -> bool:
    """True when any hard stop applies. Checked before economics, always."""
    return bool(hard_stops(ctx))
