"""Rule pack boundaries (§30.1, §40.2; ADR-020).

Exit criterion: "every rule has a unit test covering both sides of its boundary."
§40.2 fixes which boundaries: "09:59:59 / 10:00:00 IST window edges; 23:49 /
23:50 PDN cutoff; ₹14,999 / ₹15,000 / ₹15,001 AFA cap".

Every compliance rule is a cliff. A test that only probes the permitted side
proves nothing — it would pass against a rule that always returns True.

The metatest at the bottom is the enforcement: it fails if a rule ships without
a boundary test, so the compliance-gate rules' "every new rule needs a
unit test covering BOTH sides of its boundary before it is activated" becomes
machinery rather than a hope.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from prayas_rulepack.predicate import safe_eval

from prayas.gate.engine import make_afa_free_cap

RULEPACK = (
    Path(__file__).resolve().parents[2]
    / "packages"
    / "prayas-rulepack"
    / "src"
    / "prayas_rulepack"
    / "rulepack.yaml"
)
PACK: dict[str, Any] = yaml.safe_load(RULEPACK.read_text(encoding="utf-8"))
RULES: dict[str, dict[str, Any]] = {r["rule_id"]: r for r in PACK["rules"]}
CAPS: dict[str, int] = {c["mcc"]: int(c["cap_paise"]) for c in PACK["afa_caps"]}

AFA_FUNCS = {"afa_free_cap": make_afa_free_cap(CAPS)}


def check(rule_id: str, ctx: dict[str, Any]) -> bool:
    passed: bool = safe_eval(RULES[rule_id]["predicate"], ctx, AFA_FUNCS)
    return passed


def hours_ago(hours: float) -> datetime:
    """A timestamp `hours` in the past, evaluated *now*.

    Must be called inside the test body, never in a `parametrize` list.
    `hours_since` reads the clock at evaluation time, while a parametrize
    argument is fixed at collection — and the suite takes over a minute to
    reach this module, which is longer than the 61-second margin the 23h59m
    boundary case allows. That drift made the case pass alone and fail in the
    full run.
    """
    return datetime.now(tz=UTC) - timedelta(hours=hours)


# ── NPCI-AUTOPAY-WINDOW: <10:00, 13:00-17:00, >21:30 IST ────────────────────


@pytest.mark.parametrize(
    ("hour_ist", "permitted"),
    [
        (9.9972, True),  # 09:59:50
        (10.0, False),  # 10:00:00 — the cliff
        (12.9972, False),  # 12:59:50
        (13.0, True),  # 13:00:00
        (16.9972, True),  # 16:59:50
        (17.0, False),  # 17:00:00
        (21.4972, False),  # 21:29:50
        (21.5, True),  # 21:30:00
        (23.99, True),
        (0.0, True),
    ],
)
def test_npci_autopay_window(hour_ist: float, permitted: bool) -> None:
    assert check("NPCI-AUTOPAY-WINDOW", {"hour_ist": hour_ist}) is permitted


# ── RBI-EMANDATE-PDN-24H ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "sent_hours_ago", "permitted"),
    [
        ("no PDN at all", None, False),
        ("23h59m ago", 23.983, False),
        ("exactly 24h ago", 24.001, True),
        ("48h ago", 48.0, True),
        ("in the future", -1.0, False),
    ],
)
def test_rbi_pdn_24h(label: str, sent_hours_ago: float | None, permitted: bool) -> None:
    """§40.2's 24-hour boundary, both sides.

    The offset is the parameter and the timestamp is built here, so the gap
    between constructing it and evaluating the rule is microseconds rather than
    the length of the whole suite.
    """
    pdn_sent_at = None if sent_hours_ago is None else hours_ago(sent_hours_ago)
    assert check("RBI-EMANDATE-PDN-24H", {"pdn_sent_at": pdn_sent_at}) is permitted


# ── NPCI-PDN-CUTOFF-2350 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("hour_ist", "is_next_day_debit", "permitted"),
    [
        (23.82, True, True),  # 23:49 — inside the cutoff
        (23.83, True, False),  # 23:50 — the cliff
        (23.99, True, False),
        (23.83, False, True),  # not a next-day debit, cutoff does not apply
        (10.0, True, True),
    ],
)
def test_npci_pdn_cutoff(hour_ist: float, is_next_day_debit: bool, permitted: bool) -> None:
    ctx = {"hour_ist": hour_ist, "is_next_day_debit": is_next_day_debit}
    assert check("NPCI-PDN-CUTOFF-2350", ctx) is permitted


# ── RBI-EMANDATE-AFA-CAP ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "amount_paise", "mcc", "permitted"),
    [
        ("₹14,999", 1_499_900, None, True),
        ("₹15,000", 1_500_000, None, True),  # <= is inclusive
        ("₹15,001", 1_500_100, None, False),  # the cliff
        ("₹14,999 unknown mcc falls back to default", 1_499_900, "5812", True),
        ("₹15,001 unknown mcc falls back to default", 1_500_100, "5812", False),
        ("₹99,999 exempt mcc", 9_999_900, "6300", True),
        ("₹1,00,000 exempt mcc", 10_000_000, "6300", True),
        ("₹1,00,001 exempt mcc", 10_000_100, "6300", False),
        ("₹15,001 exempt mcc is permitted", 1_500_100, "6012", True),
    ],
)
def test_rbi_afa_cap(label: str, amount_paise: int, mcc: str | None, permitted: bool) -> None:
    ctx = {"amount_paise": amount_paise, "mcc": mcc}
    assert check("RBI-EMANDATE-AFA-CAP", ctx) is permitted


def test_afa_cap_denies_when_no_ceiling_is_configured() -> None:
    """Fail closed: an unconfigured ceiling must not mean an unlimited one."""
    from prayas_rulepack.predicate import PredicateError

    empty = {"afa_free_cap": make_afa_free_cap({})}
    with pytest.raises(PredicateError, match="no AFA-free ceiling"):
        safe_eval(
            RULES["RBI-EMANDATE-AFA-CAP"]["predicate"], {"amount_paise": 1, "mcc": None}, empty
        )


# ── RBI-FPC-CONTACT-WINDOW: 08:00-19:00 IST ─────────────────────────────────


@pytest.mark.parametrize(
    ("hour_ist", "permitted"),
    [
        (7.9833, False),  # 07:59
        (8.0, True),  # 08:00 — the cliff
        (18.9833, True),  # 18:59
        (19.0, False),  # 19:00 — the cliff
        (2.0, False),
    ],
)
def test_rbi_fpc_contact_window(hour_ist: float, permitted: bool) -> None:
    assert check("RBI-FPC-CONTACT-WINDOW", {"hour_ist": hour_ist}) is permitted


# ── TRAI-DLT-TEMPLATE ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "ctx", "permitted"),
    [
        (
            "all satisfied",
            {"dlt_template_id": "T1", "header_series": "160", "dnd_registered": False},
            True,
        ),
        (
            "no template",
            {"dlt_template_id": None, "header_series": "160", "dnd_registered": False},
            False,
        ),
        (
            "wrong header series",
            {"dlt_template_id": "T1", "header_series": "140", "dnd_registered": False},
            False,
        ),
        (
            "DND registered",
            {"dlt_template_id": "T1", "header_series": "160", "dnd_registered": True},
            False,
        ),
    ],
)
def test_trai_dlt_template(label: str, ctx: dict[str, Any], permitted: bool) -> None:
    assert check("TRAI-DLT-TEMPLATE", ctx) is permitted


# ── DPDP-CONSENT-VALID ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "ctx", "permitted"),
    [
        ("valid consent", {"consent_ref": "c1", "consent_withdrawn": False}, True),
        ("no consent ref", {"consent_ref": None, "consent_withdrawn": False}, False),
        ("consent withdrawn", {"consent_ref": "c1", "consent_withdrawn": True}, False),
    ],
)
def test_dpdp_consent(label: str, ctx: dict[str, Any], permitted: bool) -> None:
    assert check("DPDP-CONSENT-VALID", ctx) is permitted


# ── PRAYAS-FATIGUE-CAP ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("messages_30d", "cap", "permitted"),
    [
        (2, 3, True),
        (3, 3, False),  # the cliff — strict <
        (4, 3, False),
        (0, 0, False),
    ],
)
def test_prayas_fatigue_cap(messages_30d: int, cap: int, permitted: bool) -> None:
    ctx = {"messages_30d": messages_30d, "tenant_fatigue_cap": cap}
    assert check("PRAYAS-FATIGUE-CAP", ctx) is permitted


# ── the metatest that makes the criterion enforceable ───────────────────────

TESTED_RULE_IDS = {
    "NPCI-AUTOPAY-WINDOW",
    "RBI-EMANDATE-PDN-24H",
    "NPCI-PDN-CUTOFF-2350",
    "RBI-EMANDATE-AFA-CAP",
    "RBI-FPC-CONTACT-WINDOW",
    "TRAI-DLT-TEMPLATE",
    "DPDP-CONSENT-VALID",
    "PRAYAS-FATIGUE-CAP",
}


def test_every_rule_in_the_pack_has_a_boundary_test() -> None:
    untested = set(RULES) - TESTED_RULE_IDS
    assert not untested, (
        f"Rules with no boundary test: {sorted(untested)}. Every rule needs a test "
        f"covering BOTH sides of its boundary before it is activated "
        f"(compliance-gate rules)."
    )
    assert not TESTED_RULE_IDS - set(RULES), "TESTED_RULE_IDS names a rule not in the pack"


def test_every_rule_carries_a_citation_and_as_of() -> None:
    """Invariant 10. A rule without these is an opinion, not a rule."""
    for rule_id, rule in RULES.items():
        assert rule.get("citation"), f"{rule_id} has no citation"
        assert rule.get("as_of"), f"{rule_id} has no as_of"
        assert rule.get("regulator"), f"{rule_id} has no regulator"
        assert rule.get("on_fail") in {"DENY", "DEFER", "ESCALATE_HUMAN"}, (
            f"{rule_id} has an unrecognised on_fail: {rule.get('on_fail')}"
        )


def test_every_afa_cap_carries_a_citation_and_as_of() -> None:
    """Invariant 10 applies to regulatory reference data too (ADR-021)."""
    for cap in PACK["afa_caps"]:
        assert cap.get("citation"), f"{cap['mcc']} cap has no citation"
        assert cap.get("as_of"), f"{cap['mcc']} cap has no as_of"
        assert int(cap["cap_paise"]) > 0


def test_a_default_afa_ceiling_exists() -> None:
    """Without '*' every unknown MCC would error, denying every debit."""
    assert "*" in CAPS


def test_every_predicate_passes_the_sandbox() -> None:
    """A rule that cannot be parsed would deny at runtime — catch it here."""
    from prayas_rulepack.predicate import validate

    for rule_id, rule in RULES.items():
        try:
            validate(rule["predicate"])
        except Exception as exc:
            pytest.fail(f"{rule_id} predicate rejected by the sandbox: {exc}")
