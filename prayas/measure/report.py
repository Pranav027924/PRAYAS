"""The batch report — Phase 7's artifact (Master Spec §6, §34, §35).

"A batch report with a confidence interval, produced by machinery rather than
by hand."

Two rules shape this module:

**The SRM blocks.** §34: "An SRM failure invalidates the experiment and must
block the report rather than footnote it." So `render` refuses to print an
estimate when the check fails; it prints the failure instead. A report that
buried an SRM in a footnote would be worse than no report, because it would
look complete.

**The pair is the unit.** §6: recovery and survival are "reported as a matched
pair, never separately", because "reporting recovery without survival is how a
recovery system destroys value while appearing to create it." There is
deliberately no code path that renders one without the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from prayas.measure.estimators import Interval, SrmResult
from prayas.measure.guardrails import Guardrail, NetValue, any_breached
from prayas.measure.survival import LogRankResult


@dataclass(frozen=True, slots=True)
class BatchReport:
    """Everything §6 requires, in one object that cannot be partially rendered."""

    experiment_id: str
    seed: str
    git_commit: str
    control_pct: float

    srm: SrmResult

    # §6's primary pair.
    incremental_recovery: Interval
    incremental_survival: LogRankResult
    treatment_survival_90d: float
    control_survival_90d: float

    # §6's efficiency metrics.
    attempts_per_recovery_treatment: float
    attempts_per_recovery_control: float

    net_value: NetValue
    guardrails: list[Guardrail] = field(default_factory=list)

    cuped_theta: float = 0.0
    cuped_variance_reduction: float = 0.0

    @property
    def valid(self) -> bool:
        """An SRM failure makes every downstream number meaningless."""
        return self.srm.passed

    @property
    def headline_holds(self) -> bool:
        """§6's target: both halves of the pair positive, CI excluding zero."""
        return (
            self.valid
            and self.incremental_recovery.excludes_zero
            and self.incremental_recovery.point > 0
            and self.treatment_survival_90d >= self.control_survival_90d
            and not any_breached(self.guardrails)
        )


def render(report: BatchReport) -> str:
    """The report as text. Refuses to present an estimate on an invalid split."""
    lines: list[str] = [
        f"BATCH REPORT — {report.experiment_id}",
        f"seed {report.seed} · commit {report.git_commit} · control {report.control_pct:.0%}",
        "=" * 72,
        "",
    ]

    # ── SRM gate ────────────────────────────────────────────────────────
    if not report.srm.passed:
        lines += [
            "SAMPLE RATIO MISMATCH — REPORT BLOCKED",
            "",
            f"  observed   control {report.srm.observed_control:,}"
            f"  treatment {report.srm.observed_treatment:,}",
            f"  expected   control {report.srm.expected_control:,.1f}"
            f"  treatment {report.srm.expected_treatment:,.1f}",
            f"  chi-square {report.srm.statistic:,.2f}   p = {report.srm.p_value:.2e}",
            "",
            "Assignment did not do what pre-registration said, so every estimate",
            "below it would describe a different experiment. No numbers follow.",
        ]
        return "\n".join(lines)

    lines += [
        f"SRM check passed (chi-square {report.srm.statistic:.2f}, p = {report.srm.p_value:.3f})",
        "",
        "PRIMARY — reported as a matched pair (§6)",
        "-" * 72,
    ]

    rec = report.incremental_recovery
    verdict = "excludes zero" if rec.excludes_zero else "CONTAINS ZERO"
    lines += [
        f"  incremental recovery   {rec.point:+.4f}  "
        f"95% CI [{rec.low:+.4f}, {rec.high:+.4f}]  — {verdict}",
        f"  incremental survival   treatment {report.treatment_survival_90d:.4f} vs "
        f"control {report.control_survival_90d:.4f} at 90d",
        f"                         log-rank chi-square {report.incremental_survival.statistic:.2f}, "
        f"p = {report.incremental_survival.p_value:.4f}",
        "",
    ]

    if report.cuped_variance_reduction:
        lines += [
            f"  CUPED: theta {report.cuped_theta:+.4f}, "
            f"variance reduced {report.cuped_variance_reduction:.1%}",
            "",
        ]

    lines += [
        "EFFICIENCY",
        "-" * 72,
        f"  attempts per recovery  treatment {report.attempts_per_recovery_treatment:.2f}  "
        f"control {report.attempts_per_recovery_control:.2f}",
        "",
        "NET VALUE (§35)",
        "-" * 72,
    ]

    value = report.net_value
    for label, paise in (
        ("incremental recovered", value.incremental_recovered_paise),
        ("incremental retained LTV", value.incremental_retained_ltv_paise),
        ("attempt fees", -value.attempt_fees_paise),
        ("messaging cost", -value.messaging_cost_paise),
        ("LTV lost to induced churn", -value.ltv_lost_to_churn_paise),
    ):
        lines.append(f"  {label:<28} ₹{paise / 100:>14,.2f}")
    lines += [
        f"  {'NET':<28} ₹{value.total_paise / 100:>14,.2f}",
        f"  ({value.attribution_phrase})",
        "",
        "GUARDRAILS — reported unprompted, including when unflattering (§6)",
        "-" * 72,
    ]

    for guard in report.guardrails:
        mark = "BREACH" if guard.breached else "  ok  "
        lines.append(f"  [{mark}] {guard.name:<34} {guard.detail}")

    lines += ["", "=" * 72]
    if any_breached(report.guardrails):
        lines.append("VERDICT: guardrail breached — this result must not be claimed.")
    elif report.headline_holds:
        lines.append("VERDICT: both halves of the pair positive, guardrails clear.")
    else:
        lines.append("VERDICT: no defensible effect. Reported as such.")

    return "\n".join(lines)
