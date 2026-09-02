"""The standalone audit: what the industry default costs, in violations.

Master Spec §8, and the sentence it exists to produce:

> "The default recurring-retry behaviour widely deployed today produces N debit
> attempts outside NPCI execution windows and M debits without valid 24-hour
> pre-debit notice, per 10,000 cycles."

§8 calls this "an audit finding, not a demo" and "the single artifact from this
project most likely to be read by someone who has never heard of Prayas". So it
is built to be re-run by a stranger: `prayas-audit` prints the finding, the
methodology that produced it, and the exact rule versions it was evaluated
against.

**What this measures, stated plainly because the number will be quoted.** It
runs a *documented policy* — day 1, 3 and 5 at 10:00 IST, the default in most
dunning tools — against the rule pack. It is a property of that **schedule**,
not of any particular merchant's customers: NPCI's execution windows are fixed
clock hours, so an attempt at 10:00 IST is outside them regardless of whose
account it touches. The per-10,000 figure is arithmetic on the schedule, not an
extrapolation from a sample.

**What it does not measure.** It says nothing about how often those attempts
succeed, nothing about any real merchant's traffic, and nothing about whether
any specific tool ships this exact schedule. It measures the schedule §8 names.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from prayas_rulepack.baseline import (
    BASELINE_HOUR_IST,
    BASELINE_RETRY_DAYS,
    baseline_attempts,
)
from prayas_rulepack.evaluate import evaluate
from prayas_rulepack.loader import Rule, load_rules

#: §8's unit. A rate per 10,000 cycles rather than a raw count, so the figure
#: does not depend on how many cycles the run happened to simulate.
CYCLES: Final = 10_000

WINDOW_RULE: Final = "NPCI-AUTOPAY-WINDOW"
NOTICE_RULE: Final = "RBI-EMANDATE-PDN-24H"


@dataclass(frozen=True, slots=True)
class AuditFinding:
    """§8's finding, with everything needed to reproduce it."""

    cycles: int
    attempts_per_cycle: int
    window_violations: int
    notice_violations: int
    total_attempts: int
    rules_evaluated: tuple[tuple[str, int], ...]
    as_of: str
    #: Headline rules that were **not** in force at `as_of`. A zero against one
    #: of these means "not measured", not "no violations", and the report must
    #: not let the two be confused.
    not_in_force: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """Whether both headline rules were actually evaluated."""
        return not self.not_in_force

    @property
    def sentence(self) -> str:
        """§8's sentence, with the population stated rather than implied."""
        if not self.complete:
            return (
                f"INCOMPLETE: {', '.join(self.not_in_force)} not in force on "
                f"{self.as_of}, so this run did not measure "
                f"{'them' if len(self.not_in_force) > 1 else 'it'}. "
                f"Re-run with a later --as-of."
            )
        return (
            f"The default recurring-retry behaviour widely deployed today — "
            f"retries on days {', '.join(map(str, BASELINE_RETRY_DAYS))} at "
            f"{BASELINE_HOUR_IST:.0f}:00 IST — produces "
            f"{self.window_violations:,} debit attempts outside NPCI execution "
            f"windows and {self.notice_violations:,} debits without valid "
            f"24-hour pre-debit notice, per {self.cycles:,} cycles."
        )

    def render(self) -> str:
        lines = [
            "PRAYAS COMPLIANCE AUDIT",
            "=" * 72,
            "",
            self.sentence,
            "",
            "METHOD",
            "-" * 72,
            f"  Policy under test   day {'/'.join(map(str, BASELINE_RETRY_DAYS))} "
            f"at {BASELINE_HOUR_IST:.0f}:00 IST, no pre-debit notice",
            f"  Cycles              {self.cycles:,}",
            f"  Attempts per cycle  {self.attempts_per_cycle}",
            f"  Attempts evaluated  {self.total_attempts:,}",
            f"  Rules as of         {self.as_of}",
            "",
            "RULES EVALUATED",
            "-" * 72,
        ]
        lines += [f"  {rule_id} v{version}" for rule_id, version in self.rules_evaluated]
        lines += [
            "",
            "SCOPE",
            "-" * 72,
            "  This is a property of the *schedule*, not of any merchant's customers.",
            "  NPCI's execution windows are fixed clock hours, so an attempt at",
            "  10:00 IST falls outside them whoever it debits. The per-10,000 figure",
            "  is arithmetic on the schedule, not an extrapolation from a sample.",
            "",
            "  It says nothing about how often these attempts succeed, nothing about",
            "  any real merchant's traffic, and nothing about whether any specific",
            "  product ships this exact schedule.",
            "",
            f"  Reproduce with: prayas-audit --as-of {self.as_of}",
        ]
        return "\n".join(lines)


def run_audit(*, as_of: datetime | None = None, cycles: int = CYCLES) -> AuditFinding:
    """Evaluate the industry-default policy against the pack.

    Deterministic: the schedule is fixed and the windows are fixed, so two runs
    with the same `as_of` produce the same finding. §8 asks for "reproducible
    methodology", and a number that moved between runs would not be one.
    """
    if cycles <= 0:
        raise ValueError("cycles must be positive")

    # Defaults to *now*, so the audit reflects the regulation currently in
    # force. An earlier default would quietly exclude rules whose `as_of` had
    # not yet arrived — and the first version of this audit did exactly that,
    # reporting zero window violations for a policy that fires squarely inside
    # the NPCI peak, because the window rule was dated after the audit date.
    moment = as_of or datetime.now(UTC)
    rules: list[Rule] = load_rules(
        action_type="debit_attempt", rail="upi_autopay", as_of=moment.date()
    )

    # A finding that silently omitted the rule it is chiefly about would be
    # worse than no finding. If either headline rule is not in force at
    # `as_of`, say so rather than reporting a zero that means "not measured".
    loaded = {r.rule_id for r in rules}
    not_in_force = tuple(sorted({WINDOW_RULE, NOTICE_RULE} - loaded))

    attempts = baseline_attempts(moment, send_pdn=False)
    window_failures = notice_failures = 0

    for attempt in attempts:
        context = {
            "hour_ist": attempt["hour_ist"],
            "pdn_sent_at": attempt["pdn_sent_at"],
            "is_next_day_debit": False,
            "amount_paise": 49_900,
            "mcc": "5812",
            "consent_ref": "consent_1",
            "consent_withdrawn": False,
            "dlt_template_id": "T1",
            "header_series": "160",
            "dnd_registered": False,
            "messages_30d": 0,
            "tenant_fatigue_cap": 3,
        }
        for check in evaluate(rules, context).failures:
            if check.rule_id == WINDOW_RULE:
                window_failures += 1
            elif check.rule_id == NOTICE_RULE:
                notice_failures += 1

    per_cycle = len(attempts)
    return AuditFinding(
        not_in_force=not_in_force,
        cycles=cycles,
        attempts_per_cycle=per_cycle,
        window_violations=window_failures * cycles,
        notice_violations=notice_failures * cycles,
        total_attempts=per_cycle * cycles,
        rules_evaluated=tuple((r.rule_id, r.version) for r in rules),
        as_of=moment.date().isoformat(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit the industry-default retry policy against Indian e-mandate rules."
    )
    parser.add_argument(
        "--as-of",
        type=lambda s: datetime.fromisoformat(s).replace(tzinfo=UTC),
        default=None,
        help="Evaluate the rule versions in force on this date (YYYY-MM-DD).",
    )
    parser.add_argument("--cycles", type=int, default=CYCLES)
    args = parser.parse_args()

    # This is a command-line report meant to be read by a person or piped to a
    # file, so stdout is the output. The structured-logging rule that governs
    # the service does not apply to a CLI whose entire purpose is a printed
    # finding.
    sys.stdout.write(run_audit(as_of=args.as_of, cycles=args.cycles).render() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
