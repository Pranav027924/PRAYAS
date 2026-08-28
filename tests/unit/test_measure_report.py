"""The batch report — Phase 7's artifact (§6, §34, §35)."""

from __future__ import annotations

import pytest

from prayas.measure.estimators import Interval, srm_check
from prayas.measure.guardrails import (
    NetValue,
    complaint_rate,
    compliance_violations,
    message_volume,
    net_value_guardrail,
    revocation_rate,
)
from prayas.measure.report import BatchReport, render
from prayas.measure.survival import LogRankResult


def _net(churn_paise: int = 100_00) -> NetValue:
    return NetValue(
        incremental_recovered_paise=5_000_00,
        incremental_retained_ltv_paise=2_000_00,
        attempt_fees_paise=20_00,
        messaging_cost_paise=10_00,
        ltv_lost_to_churn_paise=churn_paise,
    )


def _report(
    *,
    control_n: int = 2000,
    treatment_n: int = 8000,
    control_pct: float = 0.20,
    recovery: Interval | None = None,
    churn_paise: int = 100_00,
    revocation_treatment: float = 0.06,
) -> BatchReport:
    value = _net(churn_paise)
    guardrails = [
        compliance_violations(0),
        revocation_rate(treatment=revocation_treatment, control=0.07),
        message_volume(2.1, fatigue_cap=3.0),
        net_value_guardrail(value),
        complaint_rate(treatment=0.004, control=0.01),
    ]
    return BatchReport(
        experiment_id="exp_demo",
        seed="committed_seed",
        git_commit="a1b2c3d",
        control_pct=control_pct,
        srm=srm_check(control_n, treatment_n, control_pct),
        incremental_recovery=recovery or Interval(0.0412, 0.0233, 0.0591),
        incremental_survival=LogRankResult(
            statistic=5.31, p_value=0.0212, observed_a=118, expected_a=139.4
        ),
        treatment_survival_90d=0.9143,
        control_survival_90d=0.8907,
        attempts_per_recovery_treatment=1.83,
        attempts_per_recovery_control=3.41,
        net_value=value,
        guardrails=guardrails,
        cuped_theta=0.61,
        cuped_variance_reduction=0.38,
    )


def test_an_srm_failure_blocks_the_report_entirely() -> None:
    """§34: "must block the report rather than footnote it".

    The estimates must not appear at all — a report that printed them with a
    warning would look complete, which is worse than printing nothing.
    """
    broken = _report(control_n=6000, treatment_n=4000, control_pct=0.20)
    assert not broken.valid

    text = render(broken)
    assert "SAMPLE RATIO MISMATCH — REPORT BLOCKED" in text
    assert "No numbers follow." in text
    # None of the estimates leaked through.
    assert "incremental recovery" not in text
    assert "NET VALUE" not in text
    assert "0.0412" not in text


def test_a_valid_report_presents_the_pair_together() -> None:
    """§6: recovery and survival "reported as a matched pair, never separately"."""
    text = render(_report())

    assert "SRM check passed" in text
    assert "incremental recovery" in text
    assert "incremental survival" in text
    # The pair appears under one heading, not in separate sections.
    primary = text.split("PRIMARY")[1].split("EFFICIENCY")[0]
    assert "recovery" in primary and "survival" in primary


def test_the_report_states_what_recovery_alone_would_have_claimed() -> None:
    """§35: the composite must be shown against the recovery-only figure.

    Asserted in the direction that actually applies to each case rather than
    assuming one — the default fixture has retention holding, so recovery-only
    understates; it is the churn case that overstates.
    """
    healthy = render(_report())
    assert "recovery alone would claim" in healthy
    assert "understating by" in healthy

    churned = render(_report(churn_paise=9_000_00))
    assert "recovery alone would claim" in churned
    assert "overstating by" in churned


def test_a_guardrail_breach_changes_the_verdict() -> None:
    """An unflattering result must be stated, not buried."""
    breached = _report(churn_paise=9_000_00)  # churn swallows the recovery
    text = render(breached)

    assert "[BREACH]" in text
    assert "guardrail breached — this result must not be claimed" in text
    assert not breached.headline_holds


def test_every_guardrail_is_printed_even_when_all_pass() -> None:
    """§6: "reported unprompted". Silence on a passing guardrail is not enough."""
    text = render(_report())
    for name in (
        "compliance_violations",
        "mandate_revocation_rate",
        "message_volume_per_customer_cycle",
        "net_value",
        "complaint_or_optout_rate",
    ):
        assert name in text, f"{name} was not reported"


def test_an_interval_containing_zero_is_reported_as_no_effect() -> None:
    """The report must be able to say nothing happened."""
    null_result = _report(recovery=Interval(0.0031, -0.0122, 0.0184))
    text = render(null_result)

    assert "CONTAINS ZERO" in text
    assert "no defensible effect" in text
    assert not null_result.headline_holds


def test_headline_requires_survival_not_to_regress() -> None:
    """Recovery up but survival down is not a win (§6's matched pair)."""
    report = _report()
    regressed = BatchReport(
        experiment_id=report.experiment_id,
        seed=report.seed,
        git_commit=report.git_commit,
        control_pct=report.control_pct,
        srm=report.srm,
        incremental_recovery=report.incremental_recovery,
        incremental_survival=report.incremental_survival,
        treatment_survival_90d=0.8500,  # below control
        control_survival_90d=0.8907,
        attempts_per_recovery_treatment=report.attempts_per_recovery_treatment,
        attempts_per_recovery_control=report.attempts_per_recovery_control,
        net_value=report.net_value,
        guardrails=report.guardrails,
    )
    assert report.headline_holds
    assert not regressed.headline_holds, "survival regressed but the headline still held"


def test_report_reports_cuped_when_it_was_applied() -> None:
    text = render(_report())
    assert "CUPED" in text
    assert "variance reduced" in text


def test_provenance_appears_in_the_header() -> None:
    """§33's pre-registration is only checkable if the report carries it."""
    text = render(_report())
    assert "committed_seed" in text
    assert "a1b2c3d" in text
    assert "exp_demo" in text


def test_efficiency_metric_is_reported() -> None:
    """§6: "Attempts per recovery: baseline burns ~3.4; target below 2.0"."""
    text = render(_report())
    assert "attempts per recovery" in text
    assert "1.83" in text and "3.41" in text


def test_srm_boundary_is_respected() -> None:
    """A split within noise passes; a real mismatch does not."""
    assert _report(control_n=2010, treatment_n=7990).valid
    assert not _report(control_n=2600, treatment_n=7400).valid


def test_net_value_arithmetic_matches_section_35() -> None:
    value = _net()
    expected = 5_000_00 + 2_000_00 - 20_00 - 10_00 - 100_00
    assert value.total_paise == expected
    assert value.overstatement_paise == 5_000_00 - expected
    assert value.total_paise == pytest.approx(expected)


def test_attribution_phrase_handles_both_signs() -> None:
    """Regression: "overstating by ₹-1,870.00" is not a sentence.

    §35's comparison can run either way. When retained LTV outweighs the costs,
    recovery-only *understates* the honest figure, and the report has to say so
    rather than printing a negative overstatement for a reviewer to decode.
    """
    understates = _net(churn_paise=100_00)
    assert understates.total_paise > understates.recovery_only_paise
    assert "understating by ₹1,870.00" in understates.attribution_phrase
    assert "-₹" not in understates.attribution_phrase

    overstates = _net(churn_paise=9_000_00)
    assert overstates.total_paise < overstates.recovery_only_paise
    assert "overstating by ₹7,030.00" in overstates.attribution_phrase

    exact = NetValue(
        incremental_recovered_paise=1_000_00,
        incremental_retained_ltv_paise=0,
        attempt_fees_paise=0,
        messaging_cost_paise=0,
        ltv_lost_to_churn_paise=0,
    )
    assert "matching the net figure exactly" in exact.attribution_phrase


def test_rendered_report_never_prints_a_negative_rupee_gap() -> None:
    """The bug as it appeared to a reader, asserted end to end."""
    for churn in (100_00, 9_000_00):
        text = render(_report(churn_paise=churn))
        assert "by ₹-" not in text, f"negative gap rendered at churn={churn}"
