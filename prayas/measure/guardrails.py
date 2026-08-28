"""Guardrail metrics and net value attribution (Master Spec §6, §35).

§6: guardrails are "reported unprompted, including when unflattering". That
phrasing is the specification, not decoration — a guardrail suite that only
surfaces when someone asks for it is not a guardrail. Every check here returns
a result whether it passed or failed, and the report prints all of them.

§35's composite is the honest headline:

    Net incremental value
      = incremental recovered rupees
      + incremental retained mandate LTV
      - attempt fees
      - messaging cost
      - LTV lost to induced churn

"Reporting incremental recovery alone overstates value by the amount of
retention it destroyed."

Money is integer paise throughout; rupees appear only in rendered strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class Status(StrEnum):
    PASS = "pass"
    BREACH = "breach"


@dataclass(frozen=True, slots=True)
class Guardrail:
    """One §6 guardrail, with the numbers behind its verdict."""

    name: str
    status: Status
    observed: float
    threshold: float
    detail: str

    @property
    def breached(self) -> bool:
        return self.status is Status.BREACH


def compliance_violations(count: int) -> Guardrail:
    """§6: "Any value above zero" is a failure.

    The only guardrail with a threshold of exactly zero, because a compliance
    violation is not a metric that can be traded off against recovery.
    """
    return Guardrail(
        name="compliance_violations",
        status=Status.BREACH if count > 0 else Status.PASS,
        observed=float(count),
        threshold=0.0,
        detail=(
            f"{count} action(s) fired that the gate should have denied"
            if count
            else "no gate violations"
        ),
    )


def revocation_rate(treatment: float, control: float) -> Guardrail:
    """§6: breached when treatment revocation is above control.

    This is the guardrail that makes "recovery that kills the mandate is fake
    recovery" enforceable rather than rhetorical.
    """
    return Guardrail(
        name="mandate_revocation_rate",
        status=Status.BREACH if treatment > control else Status.PASS,
        observed=treatment,
        threshold=control,
        detail=(
            f"treatment {treatment:.4f} vs control {control:.4f}"
            + (" — retention is being destroyed" if treatment > control else "")
        ),
    )


def message_volume(messages_per_customer_cycle: float, fatigue_cap: float) -> Guardrail:
    """§6: breached above the configured fatigue cap.

    The subscriber's guardrail. §5: "Don't debit me at a bad time, don't spam
    me, don't kill my mandate."
    """
    return Guardrail(
        name="message_volume_per_customer_cycle",
        status=Status.BREACH if messages_per_customer_cycle > fatigue_cap else Status.PASS,
        observed=messages_per_customer_cycle,
        threshold=fatigue_cap,
        detail=f"{messages_per_customer_cycle:.2f} messages against a cap of {fatigue_cap:.2f}",
    )


def complaint_rate(treatment: float, control: float) -> Guardrail:
    """§6: breached when complaints or opt-outs exceed control."""
    return Guardrail(
        name="complaint_or_optout_rate",
        status=Status.BREACH if treatment > control else Status.PASS,
        observed=treatment,
        threshold=control,
        detail=f"treatment {treatment:.4f} vs control {control:.4f}",
    )


@dataclass(frozen=True, slots=True)
class NetValue:
    """§35's composite, in integer paise."""

    incremental_recovered_paise: int
    incremental_retained_ltv_paise: int
    attempt_fees_paise: int
    messaging_cost_paise: int
    ltv_lost_to_churn_paise: int

    @property
    def total_paise(self) -> int:
        return (
            self.incremental_recovered_paise
            + self.incremental_retained_ltv_paise
            - self.attempt_fees_paise
            - self.messaging_cost_paise
            - self.ltv_lost_to_churn_paise
        )

    @property
    def recovery_only_paise(self) -> int:
        """What reporting recovery alone would have claimed.

        Kept so the report can show the gap §35 warns about explicitly rather
        than leaving a reader to compute it.
        """
        return self.incremental_recovered_paise

    @property
    def overstatement_paise(self) -> int:
        """Recovery-only minus the honest figure.

        Positive when reporting recovery alone would *overstate* value — the
        case §35 warns about, where induced churn eats the gain. Negative when
        retained LTV outweighs the costs, so recovery-only understates. Callers
        must not assume the sign; use `attribution_phrase` to render it.
        """
        return self.recovery_only_paise - self.total_paise

    @property
    def attribution_phrase(self) -> str:
        """The §35 comparison in words, with the sign handled honestly.

        "overstating by -₹1,870" is not a sentence a reviewer should have to
        parse. When the gap runs the other way it is an understatement, and
        saying so plainly is the point of reporting the composite at all.
        """
        gap = self.overstatement_paise
        claim = f"recovery alone would claim ₹{self.recovery_only_paise / 100:,.2f}"
        if gap > 0:
            return f"{claim}, overstating by ₹{gap / 100:,.2f}"
        if gap < 0:
            return f"{claim}, understating by ₹{-gap / 100:,.2f}"
        return f"{claim}, matching the net figure exactly"


def net_value_guardrail(value: NetValue) -> Guardrail:
    """§6: "Net value ... must be positive"."""
    total = value.total_paise
    return Guardrail(
        name="net_value",
        status=Status.BREACH if total <= 0 else Status.PASS,
        observed=float(total),
        threshold=0.0,
        detail=f"net ₹{total / 100:,.2f}; {value.attribution_phrase}",
    )


#: The order §6 lists them in, so a report reads the same way as the spec.
GUARDRAIL_NAMES: Final[tuple[str, ...]] = (
    "compliance_violations",
    "mandate_revocation_rate",
    "message_volume_per_customer_cycle",
    "net_value",
    "complaint_or_optout_rate",
)


def any_breached(guardrails: list[Guardrail]) -> bool:
    return any(g.breached for g in guardrails)
