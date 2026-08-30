"""Rail adapters (Playbook Phase 5; Master Spec §9, D2).

D2 makes the engine rail-abstracted across UPI Autopay, card e-mandate and
eNACH: "Rails differ in budget, windows, and settlement. One adapter interface
across three rails proves the engine is a platform capability, not a UPI hack."

Only UPI Autopay is implemented here. Card e-mandate and eNACH are Phase 14 —
the interface exists now so the DP never learns a rail's specifics, but no
placeholder adapter is registered for a rail that cannot yet be executed.

Times are stored UTC and evaluated IST (project standard). Rail windows are
IST-defined, so every predicate here converts before comparing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Final, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

IST: Final[tzinfo] = ZoneInfo("Asia/Kolkata")

#: §30.1 NPCI-PDN-CUTOFF-2350 — "hour_ist >= 23.83". 23:50 IST as a fraction.
PDN_CUTOFF_HOUR_IST: Final = 23 + 50 / 60


def hour_ist(when: datetime) -> float:
    """Fractional IST hour of an instant.

    Raises on a naive datetime: a naive timestamp on the money path is a bug,
    not a default. `ruff`'s DTZ rules exist for the same reason.
    """
    if when.tzinfo is None:
        raise ValueError("naive datetime — times are stored UTC and evaluated IST")
    return when.astimezone(IST).hour + when.astimezone(IST).minute / 60


def date_ist(when: datetime) -> tuple[int, int, int]:
    """IST calendar date. Used for 'next-day debit', which is a *calendar* test."""
    if when.tzinfo is None:
        raise ValueError("naive datetime — times are stored UTC and evaluated IST")
    local = when.astimezone(IST)
    return (local.year, local.month, local.day)


@runtime_checkable
class RailAdapter(Protocol):
    """What the sequencer needs to know about a rail, and nothing more."""

    @property
    def rail(self) -> str: ...

    @property
    def attempt_budget(self) -> int:
        """Executions plus retries the regulator permits per cycle."""
        ...

    @property
    def notice_lead_hours(self) -> int:
        """Minimum hours between the pre-debit notification and the debit."""
        ...

    def is_execution_legal(self, when: datetime) -> bool:
        """May a debit execute at this instant, on window grounds alone?"""
        ...

    def is_pdn_send_legal(self, send_at: datetime, debit_at: datetime) -> bool:
        """May a PDN for `debit_at` be submitted at `send_at`?"""
        ...

    @property
    def outcome_latency(self) -> timedelta:
        """How long after presentation the outcome becomes known (§9).

        **This is the member eNACH forced onto the protocol.** §9: "eNACH is
        the hardest rail and therefore the one that proves the abstraction.
        Batch semantics mean 'attempt at 09:10 on the 5th' is meaningless; you
        present into a clearing cycle and learn the outcome T+1. The rail
        adapter must express budget, windows, *and outcome latency*, which a
        UPI-only design would never surface."

        Zero for the real-time rails. Non-zero is what makes an attempt's
        outcome a thing that arrives rather than a thing that is returned, and
        every caller has to be written for the non-zero case.
        """
        ...

    def outcome_due_at(self, presented_at: datetime) -> datetime:
        """When an outcome should have arrived. Overdue past this is an alert."""
        ...


@dataclass(frozen=True, slots=True)
class UpiAutopayAdapter:
    """§1 — "one execution plus up to three retries per cycle", confined to
    "before 10:00, 13:00-17:00, and after 21:30 IST"."""

    @property
    def rail(self) -> str:
        return "upi_autopay"

    @property
    def attempt_budget(self) -> int:
        return 4  # 1 execution + 3 retries

    @property
    def notice_lead_hours(self) -> int:
        return 24  # §1, and §30.1 RBI-EMANDATE-PDN-24H

    def is_execution_legal(self, when: datetime) -> bool:
        """The NPCI non-peak execution windows.

        Boundaries are half-open and deliberately explicit: §40.2 names
        09:59:59 / 10:00:00 as a cliff, so 10:00 exactly is *not* legal.
        """
        h = hour_ist(when)
        return h < 10.0 or (13.0 <= h < 17.0) or h >= 21.5

    def is_pdn_send_legal(self, send_at: datetime, debit_at: datetime) -> bool:
        """§30.1 NPCI-PDN-CUTOFF-2350.

        A submission at or after 23:50 IST cannot be processed in time for a
        debit on the following calendar day. Sends for later debits are fine.
        """
        if hour_ist(send_at) < PDN_CUTOFF_HOUR_IST:
            return True
        return not _is_next_calendar_day(send_at, debit_at)

    @property
    def outcome_latency(self) -> timedelta:
        """§9 — settlement is real-time."""
        return timedelta(0)

    def outcome_due_at(self, presented_at: datetime) -> datetime:
        return presented_at


def _is_next_calendar_day(send_at: datetime, debit_at: datetime) -> bool:
    """True when `debit_at` falls on the IST calendar day after `send_at`."""
    send_date = date_ist(send_at)
    next_date = date_ist(send_at.astimezone(IST) + timedelta(days=1))
    debit_date = date_ist(debit_at)
    return debit_date == next_date and debit_date != send_date


@dataclass(frozen=True, slots=True)
class CardEmandateAdapter:
    """§9 — card e-mandate: continuous windows, network-dependent budget.

    The easy rail, and worth having precisely because it is easy: it is the
    control against which eNACH's awkwardness is visible. Execution is
    continuous, so `is_execution_legal` is always true on window grounds — the
    gate still applies every `rails: null` rule, and the AFA ceilings in
    particular bite here rather than in this adapter.
    """

    @property
    def rail(self) -> str:
        return "card_emandate"

    @property
    def attempt_budget(self) -> int:
        """§9 — "network-dependent; per-attempt fines for excess".

        Four matches the UPI budget rather than modelling the fine schedule.
        The *economics* of excess attempts on this rail are deferred: pricing
        them belongs in §23.1's cost model, and changing what the DP computes
        is not something to slip in beside an adapter (see PROGRESS Phase 14).
        """
        return 4

    @property
    def notice_lead_hours(self) -> int:
        return 24  # §9 — >=24h PDN on every rail

    def is_execution_legal(self, when: datetime) -> bool:
        """§9 — "continuous". No window constraint on this rail."""
        del when
        return True

    def is_pdn_send_legal(self, send_at: datetime, debit_at: datetime) -> bool:
        """No rail-specific submission constraint on cards.

        **The 24-hour lead is deliberately not checked here.** It is
        `RBI-EMANDATE-PDN-24H` in the rulepack, applying to all three rails,
        and ADR-020 makes that file the source of truth. Enforcing it here as
        well would put one compliance rule in two places, where the copies can
        drift and the ledger would cite a version that no longer describes what
        the code did. This method carries what is *particular* to the rail;
        NPCI's 23:50 cutoff is particular to UPI, and nothing is particular
        here.
        """
        del send_at, debit_at
        return True

    @property
    def outcome_latency(self) -> timedelta:
        """§9 — settles on card rails, effectively synchronous for our purpose."""
        return timedelta(0)

    def outcome_due_at(self, presented_at: datetime) -> datetime:
        return presented_at


#: §9 — eNACH clears on working days. Saturday and Sunday are not.
_ENACH_CLEARING_WEEKDAYS: Final[frozenset[int]] = frozenset({0, 1, 2, 3, 4})

#: The daily cut-off for presenting into the same clearing cycle, IST.
ENACH_PRESENTATION_CUTOFF_HOUR: Final = 13.0


@dataclass(frozen=True, slots=True)
class EnachAdapter:
    """§9's hard rail: present into a clearing cycle, learn the outcome T+1.

    **This adapter exists to be inconvenient.** §9 says so directly: eNACH "is
    the hardest rail and therefore the one that proves the abstraction", and
    that a UPI-only design "would never surface" outcome latency. Every method
    here is written so that code which assumes a debit resolves when it fires
    will visibly break rather than quietly mis-report.

    "Attempt at 09:10 on the 5th" is meaningless on this rail. What is
    meaningful is: presented before the cut-off on a clearing day, outcome
    known the next clearing day.
    """

    @property
    def rail(self) -> str:
        return "enach"

    @property
    def attempt_budget(self) -> int:
        """§9 — "presentation-window bound".

        Three, not four: each attempt consumes a whole clearing cycle, so the
        budget is bounded by how many presentations fit before the point at
        which retrying stops being collection and starts being harassment.
        """
        return 3

    @property
    def notice_lead_hours(self) -> int:
        return 24

    def is_execution_legal(self, when: datetime) -> bool:
        """§9 — "clearing-cycle bound".

        A presentation is legal on a clearing day before the cut-off. Outside
        that it is not illegal so much as *meaningless*: it would sit until the
        next cycle, and pretending otherwise is what makes a scheduler lie
        about when money moves.
        """
        local = when.astimezone(IST)
        return local.weekday() in _ENACH_CLEARING_WEEKDAYS and (
            hour_ist(when) < ENACH_PRESENTATION_CUTOFF_HOUR
        )

    def is_pdn_send_legal(self, send_at: datetime, debit_at: datetime) -> bool:
        """As for cards: the 24-hour lead is the gate's rule, not the rail's."""
        del send_at, debit_at
        return True

    @property
    def outcome_latency(self) -> timedelta:
        """§9 — "batch, ~1 working day"."""
        return timedelta(days=1)

    def outcome_due_at(self, presented_at: datetime) -> datetime:
        """The next clearing day after presentation.

        Calendar arithmetic, not `+ 24h`: a Friday presentation settles on
        Monday, and a system that expected it on Saturday would raise an
        overdue alert every weekend.
        """
        local = presented_at.astimezone(IST)
        due = local + timedelta(days=1)
        while due.weekday() not in _ENACH_CLEARING_WEEKDAYS:
            due += timedelta(days=1)
        return due.astimezone(presented_at.tzinfo or UTC)


#: Every rail the sequencer can act on (§9). Phase 14 completed the set.
ADAPTERS: Final[dict[str, RailAdapter]] = {
    "upi_autopay": UpiAutopayAdapter(),
    "card_emandate": CardEmandateAdapter(),
    "enach": EnachAdapter(),
}


def adapter_for(rail: str) -> RailAdapter:
    """Look up a rail adapter, failing loudly for one not yet implemented."""
    try:
        return ADAPTERS[rail]
    except KeyError:
        raise NotImplementedError(
            f"no adapter for rail {rail!r}; known rails are {sorted(ADAPTERS)}"
        ) from None
