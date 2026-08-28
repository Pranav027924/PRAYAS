"""Channel selection and the gate context for a notification (§24.2, §30.1).

§24.2 requires the planner choose "channel (DLT template, 160-series, DND
checked, consent referenced)". Each of those is a compliance predicate the gate
already evaluates, so this module's job is to assemble the context honestly —
not to decide anything the gate should decide.

Invariant 1 applies to notifications exactly as to debits: **no send path
bypasses the gate.** `build_context` produces what the gate evaluates; nothing
here returns a permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from prayas.domain.rails import hour_ist

SMS: Final = "sms"
WHATSAPP: Final = "whatsapp"

#: §30.1 TRAI-DLT-TEMPLATE: "header_series == '160'". The series reserved for
#: transactional messages; a promotional header on a debit notice is a
#: different regulatory animal.
TRANSACTIONAL_HEADER: Final = "160"

#: §24.2: "RBI-required fields plus a one-tap pay-now path when risk is elevated."
HIGH_RISK_THRESHOLD: Final = 0.5


class ChannelError(ValueError):
    """The notification cannot be assembled for any lawful channel."""


@dataclass(frozen=True, slots=True)
class Content:
    """The message body's required fields (§24.2).

    §16's obligations table states the requirement as "≥ 24h before every
    debit, **with opt-out**" — so the opt-out is not a courtesy, it is part of
    what makes the notice lawful. `__post_init__` refuses to build a body
    without one, because a notification that reached the gate missing it would
    be denied anyway, and building it would be planning an unlawful action.
    """

    amount_paise: int
    debit_at: datetime
    mandate_ref: str
    opt_out_path: str
    pay_now_link: str | None = None

    def __post_init__(self) -> None:
        if not self.opt_out_path:
            raise ChannelError(
                "a pre-debit notification must carry an opt-out (RBI E-mandate Framework 2026, §16)"
            )
        if not self.mandate_ref:
            raise ChannelError("the notice must identify the mandate it concerns")
        if not isinstance(self.amount_paise, int):
            raise TypeError("amount_paise must be an int - money is integer paise")

    @property
    def required_fields_present(self) -> bool:
        return bool(self.amount_paise and self.mandate_ref and self.opt_out_path)


@dataclass(frozen=True, slots=True)
class Notification:
    """One notification, ready for the gate to rule on."""

    channel: str
    dlt_template_id: str
    header_series: str
    send_at: datetime
    debit_at: datetime
    amount_paise: int
    consent_ref: str | None
    dnd_registered: bool
    includes_pay_now: bool
    content: Content

    @property
    def action_type(self) -> str:
        """The gate keys rules on this (§30.1 `applies_to`)."""
        return self.channel


def choose_channel(*, preferred: str | None, dnd_registered: bool) -> str:
    """Pick a channel. DND pushes to WhatsApp, which is consent-based.

    §30.1's TRAI-DLT-TEMPLATE denies SMS to a DND-registered number, so
    proposing SMS there would be planning an action the gate must refuse.
    Choosing WhatsApp instead is not a way around the rule — that path carries
    its own consent predicate, which the gate also checks.
    """
    if dnd_registered:
        return WHATSAPP
    if preferred in (SMS, WHATSAPP):
        return preferred
    return SMS


def assemble(
    *,
    send_at: datetime,
    debit_at: datetime,
    amount_paise: int,
    risk: float,
    consent_ref: str | None,
    dnd_registered: bool = False,
    preferred_channel: str | None = None,
    dlt_template_id: str = "DLT_PDN_STD_V3",
    mandate_ref: str = "mandate",
) -> Notification:
    """Build the notification. Does not decide whether it may be sent."""
    if not isinstance(amount_paise, int):
        raise TypeError("amount_paise must be an int - money is integer paise")

    elevated = risk >= HIGH_RISK_THRESHOLD
    content = Content(
        amount_paise=amount_paise,
        debit_at=debit_at,
        mandate_ref=mandate_ref,
        # Always present, whatever the risk. §16 makes it part of lawfulness.
        opt_out_path=f"/v1/mandates/{mandate_ref}/opt-out",
        # §24.2 / §17.1: a one-tap pay-now link only where risk justifies it.
        pay_now_link=f"/v1/cycles/{mandate_ref}/pay" if elevated else None,
    )

    return Notification(
        channel=choose_channel(preferred=preferred_channel, dnd_registered=dnd_registered),
        dlt_template_id=dlt_template_id,
        header_series=TRANSACTIONAL_HEADER,
        send_at=send_at,
        debit_at=debit_at,
        amount_paise=amount_paise,
        consent_ref=consent_ref,
        dnd_registered=dnd_registered,
        # §24.2: a one-tap path only where risk justifies the extra surface.
        includes_pay_now=elevated,
        content=content,
    )


def build_context(notification: Notification) -> dict[str, Any]:
    """The facts §30.1's notification rules evaluate against.

    Assembled from the notification alone, so what the gate rules on is exactly
    what would be sent — not a summary of it.
    """
    is_next_day = (notification.debit_at - notification.send_at).days < 1
    return {
        "hour_ist": hour_ist(notification.send_at),
        "is_next_day_debit": is_next_day,
        "dlt_template_id": notification.dlt_template_id,
        "header_series": notification.header_series,
        "dnd_registered": notification.dnd_registered,
        "consent_ref": notification.consent_ref,
        "consent_withdrawn": notification.consent_ref is None,
        "amount_paise": notification.amount_paise,
        "messages_30d": 0,
        "tenant_fatigue_cap": 3,
    }
