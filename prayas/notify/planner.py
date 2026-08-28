"""Pre-debit notification planning (Master Spec §24.2; Playbook Phase 9).

"Legally mandatory, guaranteed delivery, costs zero attempts, and lands
*before* the money is lost. Every competitor treats it as compliance overhead.
It is the highest-leverage channel in the system."

There is a sharper reason it matters here. FINDING-P8-01 showed the liquidity
model's information sits largely behind the 24-hour notice lead, where no retry
can act on it. The PDN is the one action that fires *before* that lead — so it
is the first place the hazard model can pay for itself.

Three constraints bound every send, and the planner respects all three before
optimising anything:

* **at least 24 hours before the debit** (§30.1 RBI-EMANDATE-PDN-24H);
* **not at or after 23:50 IST for a next-day debit** (§30.1 NPCI-PDN-CUTOFF-2350);
* **inside the contact window, 08:00-19:00 IST** (§30.1 RBI-FPC-CONTACT-WINDOW).

Within those, §24.2 wants the notice "as late as legally permitted, aligned to
the customer's attention pattern and the eve of predicted funding".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import numpy as np
from numpy.typing import NDArray

from prayas.domain.rails import PDN_CUTOFF_HOUR_IST, hour_ist

FloatArray = NDArray[np.float64]

#: §30.1 RBI-FPC-CONTACT-WINDOW: "hour_ist >= 8 and hour_ist < 19".
CONTACT_OPEN_IST: Final = 8.0
CONTACT_CLOSE_IST: Final = 19.0

#: §30.1 RBI-EMANDATE-PDN-24H.
MIN_NOTICE_HOURS: Final = 24

#: How far ahead of the debit the planner will consider sending. Beyond this
#: §24.2's "a notice sent 72 hours early is forgotten" applies.
MAX_NOTICE_HOURS: Final = 96


class PlannerError(ValueError):
    """The notification cannot be planned as asked."""


@dataclass(frozen=True, slots=True)
class NotificationPlan:
    """A planned send, with the reasoning attached."""

    send_at: datetime | None
    debit_at: datetime
    hours_of_notice: float | None
    risk_score: float
    suppressed_reason: str | None = None

    @property
    def will_send(self) -> bool:
        return self.send_at is not None


def is_contact_window(when: datetime) -> bool:
    """§30.1 RBI-FPC-CONTACT-WINDOW. Half-open, so 19:00 exactly is closed."""
    hour = hour_ist(when)
    return CONTACT_OPEN_IST <= hour < CONTACT_CLOSE_IST


def is_before_cutoff(send_at: datetime, debit_at: datetime) -> bool:
    """§30.1 NPCI-PDN-CUTOFF-2350.

    A submission at or after 23:50 IST cannot be processed for a next-day
    debit. Reuses the rail adapter's own boundary constant so the planner and
    the gate cannot drift apart on where the cliff is.
    """
    if hour_ist(send_at) < PDN_CUTOFF_HOUR_IST:
        return True
    return (debit_at - send_at) > timedelta(days=1)


def is_legal_send(send_at: datetime, debit_at: datetime) -> bool:
    """All three constraints. The planner never proposes a send that fails this."""
    notice = (debit_at - send_at).total_seconds() / 3600.0
    return (
        notice >= MIN_NOTICE_HOURS
        and is_contact_window(send_at)
        and is_before_cutoff(send_at, debit_at)
    )


def risk_score(survival: FloatArray, due_slot: int) -> float:
    """P(still unfunded when the debit lands), from the hazard model.

    §24.2's planner needs to know *which* upcoming debits are worth a
    carefully-timed notice. This is the pre-debit analogue of the recovery
    hazard: high score means the money is unlikely to be there.
    """
    if survival.size == 0:
        raise PlannerError("survival curve is empty")
    index = min(max(due_slot, 0), survival.size - 1)
    return float(survival[index])


def plan(
    *,
    debit_at: datetime,
    decided_at: datetime,
    predicted_funding_at: datetime | None,
    risk: float,
    messages_sent_30d: int = 0,
    fatigue_cap: int = 3,
) -> NotificationPlan:
    """Choose when to send, or decline to send at all.

    §24.6: a system that cannot select silence will over-message its way
    through its own portfolio, so exceeding the fatigue cap suppresses the send
    rather than shaving it. The suppression is returned with a reason so the
    ledger can record that doing nothing was chosen and why.

    Within the legal set, the notice is placed as close as possible to the eve
    of predicted funding — §24.2's mechanism. With no funding prediction it
    falls back to "as late as legally permitted", which is the same section's
    other stated preference.
    """
    if messages_sent_30d >= fatigue_cap:
        return NotificationPlan(
            send_at=None,
            debit_at=debit_at,
            hours_of_notice=None,
            risk_score=risk,
            suppressed_reason=(
                f"fatigue cap reached: {messages_sent_30d} messages in 30 days "
                f"against a cap of {fatigue_cap}"
            ),
        )

    candidates = [
        debit_at - timedelta(hours=h)
        for h in range(MIN_NOTICE_HOURS, MAX_NOTICE_HOURS + 1)
        if debit_at - timedelta(hours=h) >= decided_at
    ]
    legal = [c for c in candidates if is_legal_send(c, debit_at)]

    if not legal:
        return NotificationPlan(
            send_at=None,
            debit_at=debit_at,
            hours_of_notice=None,
            risk_score=risk,
            suppressed_reason="no lawful send instant between the decision and the debit",
        )

    if predicted_funding_at is None:
        # §24.2's fallback: as late as legally permitted.
        chosen = max(legal)
    else:
        # The eve of predicted funding, which is what the section argues for.
        chosen = min(legal, key=lambda c: abs((predicted_funding_at - c).total_seconds()))

    return NotificationPlan(
        send_at=chosen,
        debit_at=debit_at,
        hours_of_notice=(debit_at - chosen).total_seconds() / 3600.0,
        risk_score=risk,
    )


def naive_plan(*, debit_at: datetime, decided_at: datetime, risk: float) -> NotificationPlan:
    """The comparison: a fixed 72-hour notice, which §24.2 says is forgotten.

    Kept beside the optimiser so "optimised timing beats naive timing" is a
    measured claim rather than an assertion.
    """
    send_at = debit_at - timedelta(hours=72)
    if send_at < decided_at or not is_legal_send(send_at, debit_at):
        # Walk forward to the first lawful instant, as a real vendor would.
        for hours in range(72, MIN_NOTICE_HOURS - 1, -1):
            candidate = debit_at - timedelta(hours=hours)
            if candidate >= decided_at and is_legal_send(candidate, debit_at):
                send_at = candidate
                break
        else:
            return NotificationPlan(
                send_at=None,
                debit_at=debit_at,
                hours_of_notice=None,
                risk_score=risk,
                suppressed_reason="no lawful send instant",
            )

    return NotificationPlan(
        send_at=send_at,
        debit_at=debit_at,
        hours_of_notice=(debit_at - send_at).total_seconds() / 3600.0,
        risk_score=risk,
    )
