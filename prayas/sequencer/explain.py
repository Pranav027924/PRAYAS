"""Candidate explanation — the Phase 5 artifact.

"Given a failed cycle, print every candidate attempt time with its expected
value, and the reason the chosen one won."

This is the sequencer's answer to §32's replay requirement: "**every candidate
action with its expected value including those not chosen**". Recording only
the winner makes a decision unreviewable — a reviewer cannot tell whether the
chosen action beat a close second or a field of obviously worse options.

Rupee formatting lives here, as it does in `dp.stopping_rationale`: this output
is read by humans. The money path upstream stays in integer paise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from numpy.typing import NDArray

from prayas.domain.rails import IST
from prayas.sequencer.dp import SURVIVAL_FLOOR, Policy

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
BoolArray = NDArray[np.bool_]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One legal attempt time and what it is worth."""

    slot: int
    when: datetime
    conditional_hazard: float
    expected_value_paise: float
    cost_paise: int
    chosen: bool

    @property
    def expected_value_rupees(self) -> float:
        return self.expected_value_paise / 100


def rank_candidates(
    *,
    policy: Policy,
    budget_remaining: int,
    last_failure_slot: int,
    times: list[datetime],
    survival: FloatArray,
    legal: BoolArray,
    cost: IntArray,
    amount_paise: int,
    continuation_value_paise: int,
    dr: FloatArray,
    health: FloatArray,
    lead_slots: int,
    p_recoverable: float,
) -> list[Candidate]:
    """Every legal candidate, best first, with the chosen one flagged.

    Expected values are recomputed from §23.1's objective rather than read out
    of the DP, so the explanation is derived from the equation a reviewer can
    check, not from the optimiser's internals.
    """
    horizon = survival.shape[0]
    lo = last_failure_slot + lead_slots
    if lo >= horizon or survival[last_failure_slot] <= SURVIVAL_FLOOR:
        return []

    slots = np.arange(lo, horizon)[legal[lo:horizon]]
    if slots.size == 0:
        return []

    chosen = policy.best_slot(budget_remaining, last_failure_slot)
    total = amount_paise + continuation_value_paise

    p = (1.0 - survival[slots] / survival[last_failure_slot]) * health[slots] * p_recoverable
    ev = (
        p * total
        + (1.0 - p)
        * (policy.value[budget_remaining - 1][slots] - dr[slots] * continuation_value_paise)
        - cost[budget_remaining][slots]
    )

    candidates = [
        Candidate(
            slot=int(s),
            when=times[int(s)],
            conditional_hazard=float(p[i]),
            expected_value_paise=float(ev[i]),
            cost_paise=int(cost[budget_remaining][int(s)]),
            chosen=(chosen is not None and int(s) == chosen),
        )
        for i, s in enumerate(slots)
    ]
    candidates.sort(key=lambda c: c.expected_value_paise, reverse=True)
    return candidates


def format_explanation(candidates: list[Candidate], *, top: int = 10) -> str:
    """A table a merchant, an auditor, and a regulator can all read."""
    if not candidates:
        return "no legal candidate attempt times remain within the horizon"

    lines = [
        f"{'':2} {'slot':>5}  {'when (IST)':<17} {'p(fund|t)':>10} {'cost':>10} {'exp. value':>13}",
        "-" * 62,
    ]
    for c in candidates[:top]:
        mark = "->" if c.chosen else "  "
        lines.append(
            f"{mark} {c.slot:>5}  {c.when.astimezone(IST).strftime('%Y-%m-%d %H:%M'):<17} "
            f"{c.conditional_hazard:>10.4f} "
            f"{'₹' + format(c.cost_paise / 100, ',.2f'):>10} "
            f"{'₹' + format(c.expected_value_rupees, ',.2f'):>13}"
        )

    if len(candidates) > top:
        lines.append(f"   ... {len(candidates) - top} further legal slots, all lower")

    winner = next((c for c in candidates if c.chosen), None)
    if winner is None:
        lines.append("")
        lines.append(
            f"chosen: STOP - the best candidate is worth "
            f"₹{candidates[0].expected_value_rupees:,.2f}, which does not clear the bar"
        )
        return "\n".join(lines)

    runner_up = next((c for c in candidates if not c.chosen), None)
    lines.append("")
    if runner_up is None:
        lines.append(
            f"chosen: slot {winner.slot} at ₹{winner.expected_value_rupees:,.2f} - "
            f"the only legal candidate"
        )
    else:
        margin = winner.expected_value_rupees - runner_up.expected_value_rupees
        lines.append(
            f"chosen: slot {winner.slot} ({winner.when.astimezone(IST).strftime('%d %b %H:%M')}) "
            f"at ₹{winner.expected_value_rupees:,.2f} - beats the next best "
            f"(slot {runner_up.slot}) by ₹{margin:,.2f}, on a "
            f"{winner.conditional_hazard:.1%} chance of funding against a "
            f"{runner_up.conditional_hazard:.1%} one"
        )
    return "\n".join(lines)
