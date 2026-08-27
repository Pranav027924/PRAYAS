"""Legality mask construction (Playbook Phase 5; Master Spec §1, §9, §30.1).

The DP may only propose slots the rail and the regulator permit. That mask is
built from four constraints, each exposed as its own function returning its own
boolean array:

1. execution window   — NPCI non-peak windows (§1)
2. notice lead        — 24h between PDN and debit (§30.1 RBI-EMANDATE-PDN-24H)
3. PDN cutoff         — 23:50 IST submission deadline (§30.1 NPCI-PDN-CUTOFF-2350)
4. cycle deadline     — the debit cannot outlive its cycle

They are separate because the Phase 5 exit criterion requires the mask be
"verified against every rail constraint **independently**" — a combined mask
can be right for the wrong reason, and a single AND of four predicates hides
which one is doing the work.

**This mask is not the compliance gate.** It is the sequencer's own view of
what is worth *considering*; §30's gate re-evaluates every rule at fire time
and is the only thing authorised to permit a debit (Invariant 1). Duplication
here is deliberate: proposing an action the gate will deny wastes a decision,
but the mask being wrong can never cause an unlawful debit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import numpy as np
from numpy.typing import NDArray

from prayas.domain.rails import RailAdapter

#: Appendix C — `sequencer.slot_minutes: 60`, `horizon_hours: 720`.
SLOT_MINUTES: Final = 60
DEFAULT_HORIZON_SLOTS: Final = 720

BoolArray = NDArray[np.bool_]


def slot_times(
    anchor: datetime, horizon_slots: int, slot_minutes: int = SLOT_MINUTES
) -> list[datetime]:
    """The instant each slot index refers to. Slot 0 is the anchor itself."""
    if anchor.tzinfo is None:
        raise ValueError("anchor must be timezone-aware — times are stored UTC")
    if horizon_slots <= 0:
        raise ValueError(f"horizon_slots must be positive, got {horizon_slots}")
    return [anchor + timedelta(minutes=slot_minutes * i) for i in range(horizon_slots)]


def execution_window_mask(adapter: RailAdapter, times: list[datetime]) -> BoolArray:
    """Constraint 1 — the rail's non-peak execution windows (§1)."""
    return np.array([adapter.is_execution_legal(t) for t in times], dtype=np.bool_)


def notice_lead_mask(
    adapter: RailAdapter, times: list[datetime], *, decided_at: datetime
) -> BoolArray:
    """Constraint 2 — a debit needs `notice_lead_hours` of prior notice.

    Measured from `decided_at`, because the earliest a PDN can go out is the
    moment the decision is made. A slot closer than the lead time cannot be
    given lawful notice however the schedule is arranged.
    """
    earliest = decided_at + timedelta(hours=adapter.notice_lead_hours)
    return np.array([t >= earliest for t in times], dtype=np.bool_)


def pdn_cutoff_mask(
    adapter: RailAdapter, times: list[datetime], *, decided_at: datetime
) -> BoolArray:
    """Constraint 3 — §30.1's 23:50 IST submission cutoff.

    A debit slot survives if *some* lawful send instant exists for it. The send
    window is `[decided_at, debit - lead]`; a slot is excluded only when every
    instant in that window is inside the cutoff blackout for that debit.

    Checking both ends is sufficient: the blackout is a suffix of each IST day,
    so if the latest permissible send is blacked out but the earliest is not,
    a lawful instant exists between them.
    """
    lead = timedelta(hours=adapter.notice_lead_hours)
    out = np.zeros(len(times), dtype=np.bool_)

    for i, debit_at in enumerate(times):
        latest_send = debit_at - lead
        if latest_send < decided_at:
            # No send window at all; constraint 2 already excludes this slot.
            continue
        if adapter.is_pdn_send_legal(latest_send, debit_at):
            out[i] = True
        else:
            out[i] = adapter.is_pdn_send_legal(decided_at, debit_at)

    return out


def deadline_mask(times: list[datetime], *, deadline_at: datetime) -> BoolArray:
    """Constraint 4 — the cycle's own deadline.

    §36 gives `cycles.deadline_at`; §23.4 makes passing it a hard stop. A debit
    at or after the deadline is not a late attempt, it is a different cycle.
    """
    return np.array([t < deadline_at for t in times], dtype=np.bool_)


@dataclass(frozen=True, slots=True)
class LegalityMask:
    """The combined mask plus each component, so a rejection can be explained.

    The components are retained deliberately: "why was 09:00 tomorrow not
    considered?" is a question a merchant and an auditor both ask, and
    reconstructing it from the AND alone is guesswork.
    """

    combined: BoolArray
    execution_window: BoolArray
    notice_lead: BoolArray
    pdn_cutoff: BoolArray
    deadline: BoolArray
    times: list[datetime]

    def why_excluded(self, slot: int) -> list[str]:
        """Every constraint that rejects this slot, not merely the first."""
        if not 0 <= slot < len(self.times):
            raise IndexError(f"slot {slot} outside horizon of {len(self.times)}")
        reasons = []
        if not self.execution_window[slot]:
            reasons.append("outside rail execution window")
        if not self.notice_lead[slot]:
            reasons.append("inside the notice lead time")
        if not self.pdn_cutoff[slot]:
            reasons.append("no lawful PDN send instant (23:50 IST cutoff)")
        if not self.deadline[slot]:
            reasons.append("at or beyond the cycle deadline")
        return reasons

    @property
    def legal_slots(self) -> NDArray[np.int64]:
        return np.flatnonzero(self.combined).astype(np.int64)


def build_mask(
    adapter: RailAdapter,
    *,
    decided_at: datetime,
    deadline_at: datetime,
    horizon_slots: int = DEFAULT_HORIZON_SLOTS,
    slot_minutes: int = SLOT_MINUTES,
) -> LegalityMask:
    """Assemble all four constraints over the horizon."""
    times = slot_times(decided_at, horizon_slots, slot_minutes)

    window = execution_window_mask(adapter, times)
    lead = notice_lead_mask(adapter, times, decided_at=decided_at)
    cutoff = pdn_cutoff_mask(adapter, times, decided_at=decided_at)
    deadline = deadline_mask(times, deadline_at=deadline_at)

    return LegalityMask(
        combined=window & lead & cutoff & deadline,
        execution_window=window,
        notice_lead=lead,
        pdn_cutoff=cutoff,
        deadline=deadline,
        times=times,
    )
