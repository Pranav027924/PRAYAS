"""The three screens' payloads (Master Spec §6, §32; Phase 16).

"Three purposeful screens. Not a dashboard."

Each function here assembles what one screen shows and nothing else. They are
separate from the HTTP layer so the *content* can be tested without a client,
and separate from each other because a shared "console context" object is how
three purposeful screens become a dashboard.

**Guardrails sit beside the headline, structurally.** §6: they are "reported
unprompted, including when unflattering", and "reporting recovery without
survival is how a recovery system destroys value while appearing to create
it." So `batch_result` returns them at the same nesting level as the headline
pair — not under a `details` key a template can forget to render. A payload
that cannot express the metrics without the guardrails cannot be presented
without them either.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.measure.guardrails import any_breached
from prayas.measure.replay import Replay, replay_decision
from prayas.measure.report import BatchReport


class ScreenError(RuntimeError):
    """The screen cannot be assembled from what is available."""


# ── screen 1: batch result ─────────────────────────────────────────────────


def batch_result(report: BatchReport) -> dict[str, Any]:
    """§6's headline pair, its efficiency metrics, and its guardrails.

    The pair is emitted as one object rather than two fields. §6 is explicit
    that recovery and survival are "reported as a matched pair, never
    separately", and a payload that made it possible to render one without the
    other would make it possible to *ship* one without the other.
    """
    if not report.guardrails:
        raise ScreenError(
            "§6 requires guardrails reported unprompted; a batch result "
            "without them is not a batch result"
        )

    return {
        "experiment_id": report.experiment_id,
        "seed": report.seed,
        "git_commit": report.git_commit,
        "control_pct": report.control_pct,
        # An SRM failure makes every number below it meaningless, so it is
        # stated first rather than buried in a footnote.
        "valid": report.valid,
        "srm": {
            "passed": report.srm.passed,
            "p_value": report.srm.p_value,
        },
        "headline": {
            "holds": report.headline_holds,
            "recovery": {
                "point": report.incremental_recovery.point,
                "low": report.incremental_recovery.low,
                "high": report.incremental_recovery.high,
                "excludes_zero": report.incremental_recovery.excludes_zero,
            },
            "survival": {
                "treatment_90d": report.treatment_survival_90d,
                "control_90d": report.control_survival_90d,
                "p_value": report.incremental_survival.p_value,
            },
        },
        "efficiency": {
            "attempts_per_recovery_treatment": report.attempts_per_recovery_treatment,
            "attempts_per_recovery_control": report.attempts_per_recovery_control,
        },
        # Same nesting level as `headline`. Deliberate: §6's guardrails are not
        # supporting detail.
        "guardrails": [
            {
                "name": g.name,
                "status": str(g.status),
                "observed": g.observed,
                "threshold": g.threshold,
                "detail": g.detail,
            }
            for g in report.guardrails
        ],
        "guardrails_breached": any_breached(report.guardrails),
        # §6: "Recovered - fees - messaging - LTV lost to induced churn; must be
        # positive." Both figures are shown, because the difference between
        # them is exactly what a recovery-only number conceals.
        "net_value_paise": report.net_value.total_paise,
        "recovery_only_paise": report.net_value.recovery_only_paise,
        "cuped_variance_reduction": report.cuped_variance_reduction,
    }


# ── screen 2: decision replay (the artifact) ───────────────────────────────


def _replay_payload(replay: Replay) -> dict[str, Any]:
    return {
        "decision_id": replay.decision_id,
        "tenant_id": replay.tenant_id,
        "chain_seq": replay.chain_seq,
        "ts": replay.ts.isoformat(),
        "action_type": replay.action_type,
        "verdict": replay.verdict,
        "rationale": replay.rationale,
        # §32 requires candidates *including those not chosen*: a reviewer
        # cannot tell whether the winner beat a close second or an empty field.
        "candidates": replay.candidate_actions,
        "chosen": replay.chosen_action,
        # Each carries a rule_id and citation — Invariant 10.
        "compliance_checks": replay.compliance_checks,
        "holdout_arm": replay.holdout_arm,
        "propensity": replay.propensity,
        "degraded": replay.degraded,
        "integrity": {
            "hash_matches": replay.hash_matches,
            "prev_hash": replay.prev_hash,
            "record_hash": replay.record_hash,
        },
        "shows_rejected_candidates": replay.shows_rejected_candidates,
    }


async def decision_replay(conn: AsyncConnection, decision_id: str) -> dict[str, Any]:
    """§32's chain: trigger → candidates with EVs → rules with citations → outcome.

    **Renders a denial exactly as fully as an allowance.** That is the case a
    compliance reviewer opens, and a screen that only rendered successes would
    be unable to answer the one question §5 gives them: "prove this action was
    lawful when it fired." A DENY is a lawful outcome and its evidence is the
    same evidence.
    """
    return _replay_payload(await replay_decision(conn, decision_id))


# ── screen 3: policy simulator ─────────────────────────────────────────────
#
# Deliberately absent from this module: any write, any scheduling, any firing.
# The simulator recomputes policy over thousands of cycles to show how the
# frontier moves; it is not a way to enact the move. See `simulator.py`, which
# imports nothing that can act, and the test asserting that.
