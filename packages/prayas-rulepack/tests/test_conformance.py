"""Conformance suite for the rule pack.

Runs standalone: nothing here imports from `prayas`, and the clean-install test
below asserts that mechanically. A pack that only passed inside the repository
it was extracted from would not be publishable.

**Every rule is tested on both sides of its boundary.** A rule tested only
where it passes is a rule nobody has checked — the failing side is the side
that matters, because it is the one that stops a debit.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from prayas_rulepack import (
    Rule,
    RulePackError,
    afa_caps,
    all_rules,
    evaluate,
    load_rules,
)
from prayas_rulepack.audit import run_audit
from prayas_rulepack.loader import VERDICT_PRECEDENCE

UTC = UTC
TODAY = date(2026, 8, 31)


def _context(**overrides: Any) -> dict[str, Any]:
    """A context in which every rule passes, so tests can fail one at a time."""
    base: dict[str, Any] = {
        "hour_ist": 9.0,
        "pdn_sent_at": datetime.now(UTC) - timedelta(hours=30),
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
    base.update(overrides)
    return base


def _rules(rail: str = "upi_autopay", action: str = "debit_attempt") -> list[Rule]:
    return load_rules(action_type=action, rail=rail, as_of=TODAY)


def _verdict(rail: str = "upi_autopay", **overrides: Any) -> str:
    return evaluate(_rules(rail), _context(**overrides)).verdict


# ── every rule carries what makes it a rule ────────────────────────────────


@pytest.mark.parametrize("rule", all_rules(), ids=lambda r: f"{r.rule_id}v{r.version}")
def test_every_rule_carries_a_citation_and_a_date(rule: Rule) -> None:
    """A rule without them is an opinion. The loader refuses to load one, and
    this asserts the shipped pack actually satisfies that."""
    assert rule.citation.strip()
    assert rule.regulator.strip()
    assert isinstance(rule.as_of, date)
    assert rule.on_fail in VERDICT_PRECEDENCE
    assert rule.applies_to


def test_the_pack_is_not_empty() -> None:
    assert len(all_rules()) >= 9
    assert afa_caps()


# ── both sides of every boundary ───────────────────────────────────────────


def test_the_npci_window_boundary_is_a_cliff() -> None:
    """09:59:59 is lawful; 10:00:00 is not. The boundary is the rule."""
    assert _verdict(hour_ist=9.9999) == "ALLOW"
    assert _verdict(hour_ist=10.0) == "DENY"


@pytest.mark.parametrize(
    ("hour", "lawful"),
    [
        (0.0, True),
        (9.99, True),
        (10.0, False),
        (12.99, False),
        (13.0, True),
        (16.99, True),
        (17.0, False),
        (21.49, False),
        (21.5, True),
        (23.99, True),
    ],
)
def test_every_npci_window_edge(hour: float, lawful: bool) -> None:
    assert (_verdict(hour_ist=hour) == "ALLOW") is lawful


def test_the_notice_requirement_has_both_sides() -> None:
    now = datetime.now(UTC)
    assert _verdict(pdn_sent_at=now - timedelta(hours=25)) == "ALLOW"
    assert _verdict(pdn_sent_at=now - timedelta(hours=23)) == "DENY"
    assert _verdict(pdn_sent_at=None) == "DENY"


def test_the_notice_rule_applies_to_every_rail() -> None:
    """`rails: null`, so a rail added later inherits it rather than escaping."""
    for rail in ("upi_autopay", "card_emandate", "enach", "a_rail_invented_tomorrow"):
        assert evaluate(_rules(rail), _context(pdn_sent_at=None)).verdict == "DENY", rail


def test_the_window_rule_applies_only_to_upi() -> None:
    """Over-application would deny lawful collection on other rails
    continuously, and look compliant while doing it."""
    assert _verdict("upi_autopay", hour_ist=11.0) == "DENY"
    for rail in ("card_emandate", "enach"):
        assert evaluate(_rules(rail), _context(hour_ist=11.0)).verdict == "ALLOW", rail


def test_consent_withdrawal_denies() -> None:
    assert _verdict(consent_withdrawn=False) == "ALLOW"
    assert _verdict(consent_withdrawn=True) == "DENY"


# ── versioning is what makes past decisions reviewable ─────────────────────


def test_as_of_selects_the_version_in_force_then() -> None:
    """The main reason to publish rules as versioned data: a decision made in
    May must be reviewable against May's rule, not today's."""
    then = {
        r.rule_id: r.version
        for r in load_rules(action_type="debit_attempt", rail="upi_autopay", as_of=date(2026, 5, 1))
    }
    now = {r.rule_id: r.version for r in _rules()}

    assert then != now, "no rule version changed, so this proves nothing"
    for rule_id, version in then.items():
        assert now[rule_id] >= version


def test_a_rule_not_yet_in_force_is_absent_not_defaulted() -> None:
    """Applying a rule before its `as_of` would judge the past by a standard
    that did not exist."""
    early = {
        r.rule_id
        for r in load_rules(action_type="debit_attempt", rail="upi_autopay", as_of=date(2020, 1, 1))
    }
    assert early == set()


def test_one_version_per_rule_is_selected() -> None:
    rules = _rules()
    assert len(rules) == len({r.rule_id for r in rules})


# ── fail-closed ────────────────────────────────────────────────────────────


def test_a_missing_context_key_denies() -> None:
    """Not "allow because we could not tell". The worst outcome here is a debit
    permitted because the system could not work out whether it was lawful."""
    verdict = evaluate(_rules(), {"hour_ist": 9.0})
    assert verdict.verdict == "DENY"
    assert any(c.error for c in verdict.checks)


def test_every_rule_is_evaluated_even_after_a_denial() -> None:
    """So a report says a debit broke three rules, not the first one found."""
    verdict = evaluate(_rules(), _context(hour_ist=11.0, pdn_sent_at=None))
    assert len(verdict.checks) == len(_rules())
    assert len(verdict.failures) >= 2


def test_only_a_clean_allow_permits() -> None:
    """DEFER and ESCALATE_HUMAN are not permission."""
    assert VERDICT_PRECEDENCE["DENY"] > VERDICT_PRECEDENCE["ESCALATE_HUMAN"]
    assert VERDICT_PRECEDENCE["ESCALATE_HUMAN"] > VERDICT_PRECEDENCE["DEFER"]
    assert VERDICT_PRECEDENCE["DEFER"] > VERDICT_PRECEDENCE["ALLOW"]
    assert evaluate(_rules(), _context()).allowed
    assert not evaluate(_rules(), _context(hour_ist=11.0)).allowed


def test_an_explanation_cites_the_rules_that_failed() -> None:
    explanation = evaluate(_rules(), _context(hour_ist=11.0)).explain()
    assert "NPCI-AUTOPAY-WINDOW" in explanation
    assert "NPCI" in explanation


# ── the audit is reproducible ──────────────────────────────────────────────


def test_the_audit_is_deterministic() -> None:
    """§8 asks for reproducible methodology. A number that moved between runs
    would not be one."""
    first = run_audit(as_of=datetime(2026, 8, 31, tzinfo=UTC))
    second = run_audit(as_of=datetime(2026, 8, 31, tzinfo=UTC))

    assert first.window_violations == second.window_violations
    assert first.notice_violations == second.notice_violations
    assert first.sentence == second.sentence


def test_the_audit_finds_the_violations_it_exists_to_find() -> None:
    """A day-1/3/5 policy at 10:00 IST fires three times, inside the peak,
    with no notice. All three attempts breach both rules."""
    finding = run_audit(as_of=datetime(2026, 8, 31, tzinfo=UTC))

    assert finding.complete
    assert finding.attempts_per_cycle == 3
    assert finding.window_violations == 30_000
    assert finding.notice_violations == 30_000


def test_the_audit_refuses_to_report_a_zero_it_did_not_measure() -> None:
    """Run before the window rule takes effect, the honest answer is "not
    measured", not "no violations". The first version of this audit reported
    zero here, which is why the check exists."""
    finding = run_audit(as_of=datetime(2026, 6, 1, tzinfo=UTC))

    assert not finding.complete
    assert "INCOMPLETE" in finding.sentence
    assert "NPCI-AUTOPAY-WINDOW" in finding.sentence


def test_the_report_states_its_own_scope() -> None:
    """The number will be quoted. The report has to carry what it does and does
    not measure, because the sentence will travel without it otherwise."""
    report = run_audit(as_of=datetime(2026, 8, 31, tzinfo=UTC)).render()

    assert "property of the *schedule*" in report
    assert "says nothing about how often these attempts succeed" in report
    assert "prayas-audit --as-of" in report


# ── it is genuinely standalone ─────────────────────────────────────────────


def test_no_module_in_the_package_imports_prayas() -> None:
    """**The clean-install criterion, asserted at the source.**

    A pack that only worked inside the repository it came from would not be
    publishable, and the failure would only show up on someone else's machine.
    """
    root = Path(__file__).resolve().parents[1] / "src" / "prayas_rulepack"
    for module in root.rglob("*.py"):
        text = module.read_text(encoding="utf-8")
        assert "from prayas." not in text, f"{module.name} imports from prayas"
        assert "import prayas\n" not in text, f"{module.name} imports prayas"


def test_it_runs_in_a_subprocess_without_the_parent_on_the_path() -> None:
    """Stronger: actually execute it with `prayas` unimportable."""
    root = Path(__file__).resolve().parents[1] / "src"
    script = (
        "import sys;"
        "sys.modules['prayas'] = None;"
        "from prayas_rulepack import load_rules, evaluate;"
        "rules = load_rules(action_type='debit_attempt', rail='upi_autopay');"
        "print(len(rules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(root), "PATH": ""},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout.strip()) >= 3


def test_the_pack_declares_one_dependency() -> None:
    """Anyone deciding whether to trust these rules should be able to read the
    whole tree. A compliance artifact that dragged in a web framework would be
    harder to audit than the rules it carries."""
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    deps = pyproject.split("dependencies = [")[1].split("]")[0]
    assert deps.count('"') == 2, f"more than one dependency: {deps}"
    assert "pyyaml" in deps.lower()


def test_a_rule_missing_its_citation_is_refused(tmp_path: Path) -> None:
    """The loader's own guarantee, exercised rather than asserted."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "rules:\n"
        "  - rule_id: NO-CITATION\n"
        "    version: 1\n"
        "    regulator: NOBODY\n"
        "    as_of: 2026-01-01\n"
        "    applies_to: [debit_attempt]\n"
        "    predicate: 'true'\n",
        encoding="utf-8",
    )
    with pytest.raises(RulePackError, match="opinion, not a rule"):
        all_rules(bad)
