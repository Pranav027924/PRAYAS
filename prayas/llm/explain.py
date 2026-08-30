"""Grounded merchant explanation on a non-PII payload (§41.3, §32, T7).

Two constraints meet here, and they pull in the same direction.

**§41.3 — residency.** "External LLM use is confined to merchant-facing
explanation on **non-PII payloads**: amounts, decline codes, model versions,
rule citations, timestamps." So the payload is built by *allowlist*, never by
redaction. A denylist answers "what did we remember to remove"; an allowlist
answers "what did we decide to send", and only the second is checkable.

**§32 / §6 — the explanation must be true.** The definition of done is that a
reviewer can "click any recovered rupee and see why it happened, with rule
citations". An explanation containing a plausible fact that is not in the
record is worse than no explanation: it is an audit trail that lies.

**So the explanation is composed here, not generated.** The text below is built
deterministically from record fields. A model may later be asked to *rephrase*
it — that is what `payload_for_provider` exists for — but the facts are fixed
before any model sees them, and `ungrounded_claims` checks what comes back.
Asking a model to author the facts and then checking them afterwards would be
the wrong way round: it makes hallucination a thing you detect rather than a
thing you prevented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

#: §41.3's allowlist, as field names on a decision record. Anything not named
#: here does not leave the region, whatever it contains.
NON_PII_FIELDS: Final[tuple[str, ...]] = (
    "action_type",
    "verdict",
    "amount_paise",
    "decline_code",
    "model_versions",
    "compliance_checks",
    "revocation_hazard",
    "continuation_value",
    "propensity",
    "degraded",
    "outcome",
    "recovered_paise",
    "ts",
    "outcome_ts",
)

#: Fields that identify a person or a mandate. Named explicitly so the test
#: suite can assert their absence rather than inferring it.
PII_FIELDS: Final[tuple[str, ...]] = (
    "customer_id",
    "customer_ref",
    "mandate_id",
    "cycle_id",
    "decision_id",
    "trigger_event_id",
    "raw_text",
    "consent_ref",
    "tenant_id",
)


class ExplanationError(ValueError):
    """The record cannot be explained safely."""


@dataclass(frozen=True, slots=True)
class Explanation:
    """A composed explanation and the fields it was built from."""

    text: str
    grounded_in: tuple[str, ...]

    def __str__(self) -> str:
        return self.text


def payload_for_provider(record: dict[str, Any]) -> dict[str, Any]:
    """The projection that may cross the region boundary (§41.3).

    Built by allowlist. A field absent from `NON_PII_FIELDS` is not included,
    regardless of what it holds — so adding a column to `decisions` cannot
    silently widen what gets sent.
    """
    payload = {key: record[key] for key in NON_PII_FIELDS if record.get(key) is not None}

    leaked = set(payload) & set(PII_FIELDS)
    if leaked:
        # Unreachable while the two constants stay disjoint. Kept because the
        # cost of the check is nothing and the cost of the mistake is a
        # residency breach (T7).
        raise ExplanationError(f"allowlist admits identifying fields: {sorted(leaked)}")
    return payload


def _rupees(paise: int) -> str:
    return f"₹{paise / 100:,.2f}"


def compose(record: dict[str, Any]) -> Explanation:
    """Build the explanation deterministically from the record.

    Every clause is emitted only when the field backing it is present, so the
    text cannot assert something the record does not contain. Rupee formatting
    lives here because this string is read by humans; the money path upstream
    stays in integer paise.
    """
    used: list[str] = []
    parts: list[str] = []

    verdict = record.get("verdict")
    action = record.get("action_type")
    if verdict and action:
        used += ["verdict", "action_type"]
        parts.append(f"{action} was {'allowed' if verdict == 'ALLOW' else 'denied'}")

    amount = record.get("amount_paise")
    if amount is not None:
        used.append("amount_paise")
        parts.append(f"for {_rupees(int(amount))}")

    code = record.get("decline_code")
    if code:
        used.append("decline_code")
        parts.append(f"after decline code {code}")

    checks = record.get("compliance_checks") or []
    citations = [c.get("rule_id") for c in checks if isinstance(c, dict) and c.get("rule_id")]
    if citations:
        used.append("compliance_checks")
        parts.append("under " + ", ".join(str(c) for c in citations))

    recovered = record.get("recovered_paise")
    if recovered is not None:
        used.append("recovered_paise")
        parts.append(f"recovering {_rupees(int(recovered))}")

    if record.get("degraded"):
        used.append("degraded")
        parts.append("while the system was running degraded")

    if not parts:
        raise ExplanationError("record carries nothing explainable")

    return Explanation(text="; ".join(parts) + ".", grounded_in=tuple(used))


#: Numbers and rule-like identifiers a claim could smuggle in.
_NUMERIC = re.compile(r"\d[\d,]*(?:\.\d+)?")
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]+(?:-[A-Z0-9]+)+\b")


def _record_tokens(record: dict[str, Any]) -> set[str]:
    """Every number and identifier the record actually contains."""
    tokens: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                walk(item)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, int):
            tokens.add(str(value))
            # An amount in paise is legitimately rendered as rupees.
            tokens.add(f"{value / 100:,.2f}")
            tokens.add(f"{value:,}")
        elif isinstance(value, float):
            tokens.add(f"{value:g}")
            tokens.add(f"{value:.3f}")
        elif isinstance(value, datetime):
            tokens.add(str(value.year))
        else:
            text = str(value)
            tokens.add(text)
            for match in _NUMERIC.finditer(text):
                tokens.add(match.group())

    walk(record)
    return tokens


def ungrounded_claims(text: str, record: dict[str, Any]) -> list[str]:
    """Tokens in `text` that the record cannot account for.

    An empty list is the Phase 13 exit criterion: "explanations contain no fact
    absent from the record". Applied to model-rephrased text as much as to
    composed text — the check is on the output, not on who wrote it.
    """
    known = _record_tokens(record)
    claims: list[str] = []

    for match in _NUMERIC.finditer(text):
        token = match.group()
        if token not in known and token.replace(",", "") not in known:
            claims.append(token)

    for match in _IDENTIFIER.finditer(text):
        if match.group() not in known:
            claims.append(match.group())

    return claims
