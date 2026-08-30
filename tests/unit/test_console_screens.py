"""Screen payloads (Master Spec §6; Phase 16).

**Phase 16 exit criterion:** "Guardrails displayed with equal prominence to
headline metrics."

Asserted *structurally* rather than by inspecting a rendered page. §6 says
guardrails are "reported unprompted, including when unflattering", and the way
that promise is broken is not by deleting them — it is by nesting them one
level deeper than the headline, where a template can quietly omit them and
nobody notices until the number is embarrassing.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from prayas.console.screens import ScreenError, batch_result
from prayas.measure.estimators import Interval, SrmResult
from prayas.measure.guardrails import Guardrail, NetValue, Status
from prayas.measure.report import BatchReport
from prayas.measure.survival import LogRankResult


def _report(*, guardrails: list[Guardrail] | None = None, breached: bool = False) -> BatchReport:
    rails = (
        guardrails
        if guardrails is not None
        else [
            Guardrail(
                name="compliance_violations",
                status=Status.BREACH if breached else Status.PASS,
                observed=1.0 if breached else 0.0,
                threshold=0.0,
                detail="any value above zero is a failure",
            ),
            Guardrail(
                name="revocation_rate",
                status=Status.PASS,
                observed=0.02,
                threshold=0.03,
                detail="below control",
            ),
        ]
    )
    return BatchReport(
        experiment_id="exp_1",
        seed="phase16",
        git_commit="abc1234",
        control_pct=0.15,
        srm=SrmResult(
            observed_control=150,
            observed_treatment=850,
            expected_control=150.0,
            expected_treatment=850.0,
            statistic=0.0,
            p_value=0.9,
        ),
        incremental_recovery=Interval(point=0.41, low=0.37, high=0.45),
        incremental_survival=LogRankResult(
            statistic=3.1, p_value=0.04, observed_a=42, expected_a=35.0
        ),
        treatment_survival_90d=0.94,
        control_survival_90d=0.91,
        attempts_per_recovery_treatment=1.51,
        attempts_per_recovery_control=8.40,
        net_value=NetValue(
            incremental_recovered_paise=5_000_000,
            incremental_retained_ltv_paise=2_000_000,
            attempt_fees_paise=200_000,
            messaging_cost_paise=50_000,
            ltv_lost_to_churn_paise=100_000,
        ),
        guardrails=rails,
    )


# ── the criterion ──────────────────────────────────────────────────────────


def test_guardrails_sit_at_the_same_level_as_the_headline() -> None:
    """**Phase 16 exit criterion.** Not nested under a `details` key a template
    can forget to render."""
    payload = batch_result(_report())

    assert "headline" in payload
    assert "guardrails" in payload
    # Same nesting depth: both are direct children of the payload.
    assert isinstance(payload["guardrails"], list)
    assert payload["guardrails"], "guardrails present but empty"
    for key in ("headline", "guardrails", "guardrails_breached"):
        assert key in payload


def test_a_report_without_guardrails_cannot_be_rendered_at_all() -> None:
    """§6's "reported unprompted" made structural: a payload that could omit
    them would eventually omit them on the run where they mattered."""
    with pytest.raises(ScreenError, match="unprompted"):
        batch_result(_report(guardrails=[]))


def test_a_breach_is_surfaced_not_buried() -> None:
    """ "Including when unflattering." A breached guardrail must be visible at
    the top level, not only inside the list someone has to read."""
    payload = batch_result(_report(breached=True))

    assert payload["guardrails_breached"] is True
    assert payload["headline"]["holds"] is False, (
        "a breached guardrail must falsify the headline, not sit beside it"
    )


def test_the_headline_pair_is_one_object() -> None:
    """§6: "reported as a matched pair, never separately". A payload that made
    it possible to render one without the other would make it possible to ship
    one without the other."""
    headline = batch_result(_report())["headline"]

    assert "recovery" in headline and "survival" in headline
    assert headline["recovery"]["point"] == 0.41
    assert headline["survival"]["treatment_90d"] == 0.94


def test_srm_validity_is_stated_before_the_numbers_it_invalidates() -> None:
    """An SRM failure makes every number below it meaningless, so it is a
    top-level field rather than a footnote."""
    payload = batch_result(_report())
    assert "valid" in payload
    assert "srm" in payload


def test_net_value_shows_both_figures_so_the_gap_is_visible() -> None:
    """§6 defines net value as recovery plus retained LTV, minus fees,
    messaging and churned LTV. §35 wants the *gap* between that and a
    recovery-only claim shown explicitly rather than left to be computed.

    The direction is deliberately not asserted: recovery-only overstates when
    churn dominates and understates when retained LTV does, and a test fixing
    the sign would encode one scenario as the truth.
    """
    payload = batch_result(_report())

    assert "net_value_paise" in payload
    assert "recovery_only_paise" in payload
    assert payload["net_value_paise"] != payload["recovery_only_paise"], (
        "the two figures coincide, so the report shows no gap to read"
    )


def test_the_honest_figure_falls_below_recovery_when_churn_dominates() -> None:
    """The case §6 is actually warning about: "reporting recovery without
    survival is how a recovery system destroys value while appearing to create
    it." When induced churn outweighs what was retained, the net must drop
    below the recovery-only claim."""
    report = _report()
    costly = replace(
        report,
        net_value=NetValue(
            incremental_recovered_paise=5_000_000,
            incremental_retained_ltv_paise=100_000,
            attempt_fees_paise=200_000,
            messaging_cost_paise=50_000,
            ltv_lost_to_churn_paise=3_000_000,
        ),
    )
    payload = batch_result(costly)

    assert payload["net_value_paise"] < payload["recovery_only_paise"]
