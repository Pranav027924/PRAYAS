"""The industry-standard day-1/3/5 retry policy (ADR-050).

Governing spec: Master Spec §8, §33.

One definition, cited by two callers that must never disagree:

* §8's shadow audit, which counts how many NPCI violations this policy produces
  and publishes the figure;
* Phase 7's **control arm**, which is the thing the treatment is measured
  against.

If those two ever drifted apart, the published claim that "the default retry
behaviour produces N violations per 10,000 cycles" would describe a policy the
experiment never actually ran, and the two numbers would be quietly
incomparable. Keeping the schedule here makes the shared dependency explicit
rather than incidental.

§33 requires the control policy be "the documented industry-standard day-1/3/5
schedule, run through the same compliance gate - so the comparison is against
realistic practice, and so §8's violation count falls out for free."
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

#: The schedule most dunning tools ship as their default. Built for card rails,
#: where retries are cheap and effectively unlimited (§1).
BASELINE_RETRY_DAYS: Final[tuple[int, ...]] = (1, 3, 5)

#: Baseline tools retry at a fixed clock time, typically business hours.
#: 10:00 IST sits squarely inside the NPCI peak window - which is the point:
#: a calendar policy has no concept of an execution window to respect.
BASELINE_HOUR_IST: Final = 10.0

#: 04:30 UTC == 10:00 IST. IST is UTC+5:30 and has no daylight saving, so the
#: offset is constant and this conversion is exact.
_BASELINE_HOUR_UTC: Final = 4
_BASELINE_MINUTE_UTC: Final = 30


def baseline_fire_times(first_failure: datetime) -> list[datetime]:
    """The instants a day-1/3/5 policy would fire at, in UTC.

    Deliberately ignores execution windows, notice lead and attempt budget.
    Modelling those would make it a different policy - the whole finding is
    that the mainstream default does not model them.
    """
    if first_failure.tzinfo is None:
        raise ValueError("first_failure must be timezone-aware - times are stored UTC")

    anchor = first_failure.astimezone(UTC)
    return [
        (anchor + timedelta(days=day)).replace(
            hour=_BASELINE_HOUR_UTC, minute=_BASELINE_MINUTE_UTC, second=0, microsecond=0
        )
        for day in BASELINE_RETRY_DAYS
    ]


def baseline_attempts(first_failure: datetime, *, send_pdn: bool = False) -> list[dict[str, Any]]:
    """The schedule as gate-evaluable contexts.

    `send_pdn=False` is the realistic default: the pre-debit notification is a
    requirement of the Indian e-mandate framework that tools designed for card
    rails do not model at all, which is precisely what §8 measures.
    """
    return [
        {
            "fire_at": fire_at,
            "hour_ist": BASELINE_HOUR_IST,
            "pdn_sent_at": fire_at - timedelta(hours=25) if send_pdn else None,
        }
        for fire_at in baseline_fire_times(first_failure)
    ]


def attempts_per_cycle() -> int:
    """How many attempts the baseline spends per cycle, unconditionally.

    Three, always - a calendar policy does not stop early, which is why §6
    reports "attempts per recovery" as an efficiency metric against it.
    """
    return len(BASELINE_RETRY_DAYS)
