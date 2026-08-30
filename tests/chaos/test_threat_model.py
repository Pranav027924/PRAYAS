"""Threat-model coverage (Master Spec §41.1).

**Phase 15 exit criterion:** "Every threat-model row has a passing test or a
documented accepted risk."

The matrix below is the whole point. It is easy to have tests for most threats
and never notice which one has none — a row silently uncovered looks exactly
like a row covered by a test somebody deleted. So each of §41.1's nine rows
must name either a test that exists or an acceptance with a reason, and a
metatest asserts the matrix stays complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Coverage:
    """One §41.1 row and how it is discharged."""

    threat: str
    #: Test files that exercise the mitigation. Paths, so their existence is
    #: checkable rather than a claim.
    tests: tuple[str, ...] = ()
    #: Why the risk is accepted instead. Mutually exclusive with `tests`.
    accepted: str | None = None

    def __post_init__(self) -> None:
        if bool(self.tests) == bool(self.accepted):
            raise ValueError(f"{self.threat}: name either tests or an acceptance, not both")


#: §41.1's nine rows, in order.
MATRIX: dict[str, Coverage] = {
    "T1": Coverage(
        threat="Unauthorised debit trigger",
        tests=(
            "tests/integration/test_gate.py",
            "tests/integration/test_executor_safety.py",
            "tests/chaos/test_killswitch.py",
        ),
    ),
    "T2": Coverage(
        threat="Cross-tenant data leakage",
        tests=(
            "tests/isolation/test_tenant_isolation.py",
            "tests/isolation/test_policy_coverage.py",
            "tests/isolation/test_memory_store.py",
        ),
    ),
    "T3": Coverage(
        threat="Ledger tampering",
        tests=("tests/integration/test_ledger_chain.py", "tests/isolation/test_forgetting.py"),
    ),
    "T4": Coverage(
        threat="Prompt injection via inbound reply",
        tests=("tests/unit/test_llm_injection.py", "tests/isolation/test_llm_policy_dsl.py"),
    ),
    "T5": Coverage(
        threat="Compliance rule tampering",
        tests=("tests/integration/test_gate_rails.py", "tests/isolation/test_llm_policy_dsl.py"),
    ),
    "T6": Coverage(
        threat="Webhook forgery",
        tests=("tests/unit/test_verify.py", "tests/integration/test_webhook_ingest.py"),
    ),
    "T7": Coverage(
        threat="PII exfiltration via LLM provider",
        tests=("tests/unit/test_llm_explain.py",),
    ),
    "T8": Coverage(
        threat="Credential compromise",
        accepted=(
            "Partially mitigated and partially accepted. Least privilege is "
            "enforced and tested: the app role owns nothing, is subject to RLS "
            "unconditionally, and holds no UPDATE or DELETE on `decisions` "
            "(asserted in tests/isolation/). Secrets arrive by environment "
            "injection only, never from files. What is NOT built is rotation, "
            "short-lived tokens, or a secrets manager — those are deployment "
            "infrastructure this repository does not provision, and claiming "
            "them from a docker-compose file would be false. Accepted until "
            "there is a deployment to harden."
        ),
    ),
    "T9": Coverage(
        threat="Denial of wallet (cost attack)",
        accepted=(
            "Structurally reduced rather than mitigated. The system makes no "
            "paid external calls: ADR-080 puts inference behind a deterministic "
            "in-repo stub with no network path, and the attempt budget is "
            "capped per cycle by the regulator's own limit and enforced in the "
            "schema (`CHECK (attempts_used <= attempt_budget)`). Per-tenant "
            "quotas and global rate limits are NOT built — they belong at an "
            "ingress this repository does not own. Accepted on the grounds "
            "that the spend surface is currently zero."
        ),
    ),
}


# ── the criterion ──────────────────────────────────────────────────────────


def test_every_threat_row_is_present() -> None:
    """**Phase 15 exit criterion.** §41.1 has nine rows; a missing one is a
    threat nobody decided about."""
    assert sorted(MATRIX) == [f"T{n}" for n in range(1, 10)]


@pytest.mark.parametrize("threat_id", sorted(MATRIX))
def test_every_threat_is_tested_or_explicitly_accepted(threat_id: str) -> None:
    row = MATRIX[threat_id]
    assert row.tests or row.accepted, f"{threat_id} is neither tested nor accepted"


@pytest.mark.parametrize("threat_id", sorted(MATRIX))
def test_every_named_test_file_exists(threat_id: str) -> None:
    """A matrix that names a deleted file is worse than an empty one: it reads
    as covered."""
    for relative in MATRIX[threat_id].tests:
        assert (REPO / relative).is_file(), f"{threat_id} names a missing file: {relative}"


@pytest.mark.parametrize("threat_id", sorted(MATRIX))
def test_an_acceptance_says_what_is_not_built(threat_id: str) -> None:
    """An acceptance that only says "accepted" is a shrug. It has to name the
    gap, so a reader can tell what would close it."""
    accepted = MATRIX[threat_id].accepted
    if accepted is None:
        return
    assert len(accepted) > 120, f"{threat_id}'s acceptance is too thin to review"
    assert "NOT built" in accepted or "not built" in accepted


def test_the_accepted_rows_are_the_ones_we_expect() -> None:
    """Pins which risks are accepted. Accepting a new one should be a visible
    diff, not a quiet edit to a dictionary."""
    accepted = {k for k, v in MATRIX.items() if v.accepted}
    assert accepted == {"T8", "T9"}, (
        "the set of accepted risks changed — this should be a deliberate, "
        "reviewed decision rather than a side effect"
    )
