"""Shadow-mode evaluation and the §8 audit artifact.

§30.4: "The same engine evaluates any policy without firing."

§8 is the reason this exists in Phase 2 rather than later:

    Run the **documented industry-standard retry policy** — day 1/3/5, the
    default behaviour in most dunning tools — through the compliance gate in
    shadow mode against the current e-mandate framework, and count the
    violations.

    "The default recurring-retry behaviour widely deployed today produces N
    debit attempts outside NPCI execution windows and M debits without valid
    24-hour pre-debit notice, per 10,000 cycles."

That is an audit finding, not a demo, and it is the one output of this project
most likely to be read by someone who has never heard of Prayas. Nothing here
fires anything: it evaluates, counts, and reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.gate.engine import ALLOW, evaluate

#: IST is UTC+5:30. Times are stored UTC and evaluated IST (project standard).
IST_OFFSET: Final = timedelta(hours=5, minutes=30)

#: The day-1/3/5 schedule most dunning tools ship as their default.
BASELINE_RETRY_DAYS: Final[tuple[int, ...]] = (1, 3, 5)

#: Baseline tools retry at a fixed clock time, typically business hours.
#: 10:00 IST sits squarely inside the NPCI peak window, which is the point.
BASELINE_HOUR_IST: Final = 10.0


def hour_ist(moment: datetime) -> float:
    """Fractional hour in IST. §30.1's predicates are written against this."""
    local = moment.astimezone(UTC) + IST_OFFSET
    return local.hour + local.minute / 60.0 + local.second / 3600.0


@dataclass
class ShadowReport:
    """Violation counts, attributable to specific rules."""

    cycles: int = 0
    attempts: int = 0
    allowed: int = 0
    violations_by_rule: dict[str, int] = field(default_factory=dict)
    violations_by_verdict: dict[str, int] = field(default_factory=dict)

    @property
    def violating_attempts(self) -> int:
        return self.attempts - self.allowed

    def per_10k_cycles(self, rule_id: str) -> float:
        if self.cycles == 0:
            return 0.0
        return self.violations_by_rule.get(rule_id, 0) * 10_000 / self.cycles

    def record(self, verdict: str, denied: list[str]) -> None:
        self.attempts += 1
        if verdict == ALLOW:
            self.allowed += 1
            return
        self.violations_by_verdict[verdict] = self.violations_by_verdict.get(verdict, 0) + 1
        for rule_id in denied:
            self.violations_by_rule[rule_id] = self.violations_by_rule.get(rule_id, 0) + 1

    def summary(self) -> str:
        """The §8 sentence, with the numbers filled in."""
        window = self.per_10k_cycles("NPCI-AUTOPAY-WINDOW")
        pdn = self.per_10k_cycles("RBI-EMANDATE-PDN-24H")
        return (
            f"The default recurring-retry behaviour widely deployed today produces "
            f"{window:.0f} debit attempts outside NPCI execution windows and "
            f"{pdn:.0f} debits without valid 24-hour pre-debit notice, per 10,000 cycles."
        )


def baseline_attempts(first_failure: datetime, *, send_pdn: bool = False) -> list[dict[str, Any]]:
    """The day-1/3/5 schedule a mainstream dunning tool would produce.

    `send_pdn=False` is the realistic default: the pre-debit notification is a
    requirement of the Indian e-mandate framework that tools designed for card
    rails do not model at all, which is precisely what §8 measures.
    """
    attempts: list[dict[str, Any]] = []
    for day in BASELINE_RETRY_DAYS:
        fire_at = (first_failure + timedelta(days=day)).replace(
            hour=4, minute=30, second=0, microsecond=0
        )  # 04:30 UTC == 10:00 IST
        attempts.append(
            {
                "fire_at": fire_at,
                "hour_ist": BASELINE_HOUR_IST,
                "pdn_sent_at": fire_at - timedelta(hours=25) if send_pdn else None,
            }
        )
    return attempts


async def evaluate_baseline_policy(
    conn: AsyncConnection,
    *,
    cycles: int = 10_000,
    as_of: date | None = None,
    send_pdn: bool = False,
    amount_paise: int = 49_900,
    mcc: str = "5812",
) -> ShadowReport:
    """Run the baseline policy through the real gate, firing nothing (§30.4).

    Cycles are spread across a month so day-of-week and day-of-month effects do
    not all land on one clock position — a single simulated date would make the
    violation count an artefact of that date.
    """
    report = ShadowReport()
    as_of = as_of or datetime.now(tz=UTC).date()
    origin = datetime(2026, 6, 1, tzinfo=UTC)

    for index in range(cycles):
        report.cycles += 1
        first_failure = origin + timedelta(days=index % 28, minutes=index % 60)

        for attempt in baseline_attempts(first_failure, send_pdn=send_pdn):
            result = await evaluate(
                conn,
                action_type="debit_attempt",
                rail="upi_autopay",
                ctx={
                    "hour_ist": attempt["hour_ist"],
                    "pdn_sent_at": attempt["pdn_sent_at"],
                    "is_next_day_debit": False,
                    "amount_paise": amount_paise,
                    "mcc": mcc,
                    "consent_ref": "consent_baseline",
                    "consent_withdrawn": False,
                },
                as_of=as_of,
            )
            report.record(result.verdict, result.denied_rules())

    return report
