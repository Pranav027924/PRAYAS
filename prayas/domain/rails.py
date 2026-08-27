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
from datetime import datetime, tzinfo
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


def _is_next_calendar_day(send_at: datetime, debit_at: datetime) -> bool:
    """True when `debit_at` falls on the IST calendar day after `send_at`."""
    from datetime import timedelta

    send_date = date_ist(send_at)
    next_date = date_ist(send_at.astimezone(IST) + timedelta(days=1))
    debit_date = date_ist(debit_at)
    return debit_date == next_date and debit_date != send_date


#: The rails the sequencer can currently act on. Phase 14 adds the other two.
ADAPTERS: Final[dict[str, RailAdapter]] = {"upi_autopay": UpiAutopayAdapter()}


def adapter_for(rail: str) -> RailAdapter:
    """Look up a rail adapter, failing loudly for one not yet implemented."""
    try:
        return ADAPTERS[rail]
    except KeyError:
        raise NotImplementedError(
            f"no adapter for rail {rail!r}; card_emandate and enach arrive in Phase 14"
        ) from None
