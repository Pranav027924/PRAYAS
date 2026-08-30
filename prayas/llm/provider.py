"""The inference boundary (Master Spec §41.3, §19; ADR-080).

§41.3: "Reply parsing runs on **in-region inference** — a self-hosted
multilingual model rather than an external hosted API, because the message
content is personal data." So the interface here has one job: make the seam
where that model plugs in explicit, and make it impossible for the PII path to
reach an external provider by accident.

**Two providers, and the type system keeps them apart.** `InRegionProvider` may
see reply text. `ExternalProvider` may not, ever — §41.3 confines external use
to "merchant-facing explanation on non-PII payloads". They are separate
protocols rather than one with a flag, because a flag can be passed wrongly and
a type cannot.

**The stub is the phase's provider (ADR-080).** Every Phase 13 exit criterion
is about *our* handling — schema rejection, bounded influence, PII containment
— and none is about model quality. A deterministic stub makes the injection
suite exhaustive and repeatable, which a sampled model cannot be. It is also
what keeps CI free of a network dependency and of §41.1's T9, denial of wallet.

**§19: "LLM provider down → no explanations, no reply parsing; never blocks a
money decision."** So a provider failure is a normal, typed outcome here, not
an exception that propagates. `ProviderUnavailable` is a value.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Final, Protocol

#: Cap on how much text is handed to a parser. A reply is an SMS; anything
#: vastly longer is either a bug or an attempt to bury an instruction past
#: whatever truncation the caller applies.
MAX_REPLY_CHARS: Final = 1600


@dataclass(frozen=True, slots=True)
class ProviderUnavailable:
    """§19's degradation, as a value rather than an exception."""

    reason: str


#: What a provider returns: a decoded object of unknown shape, or unavailability.
#: Deliberately `Any` — this is untrusted input, and pretending otherwise here
#: would move the lie upstream of the validator that exists to catch it.
ProviderResult = Any | ProviderUnavailable


class InRegionProvider(Protocol):
    """Inference that may see personal data. Must run in-region (§41.3)."""

    def parse_reply(self, text: str) -> ProviderResult:
        """Text in, decoded JSON out. **No tools. No side effects.**"""
        ...


class ExternalProvider(Protocol):
    """Inference that may **never** see personal data (§41.3, T7).

    Separate from `InRegionProvider` so that handing reply text to an external
    provider is a type error rather than a review comment.
    """

    def explain(self, payload: dict[str, Any]) -> ProviderResult: ...


# ── the deterministic stub ─────────────────────────────────────────────────

#: Day-of-month spelled in the ways an Indian SMS actually spells it —
#: "5 tarikh", "5th", "date 5". Deliberately narrow: this stub exists to drive
#: the boundary's tests, not to be a language model.
_DAY_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b(\d{1,2})\s*(?:tarikh|tareekh|taarikh)\b", re.IGNORECASE),
    re.compile(r"\b(?:date|dt)\.?\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\b", re.IGNORECASE),
)

_OPT_OUT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bstop\b", re.IGNORECASE),
    re.compile(r"\bunsubscribe\b", re.IGNORECASE),
    re.compile(r"\bband\s*kar", re.IGNORECASE),
    re.compile(r"\bmat\s*bhejo\b", re.IGNORECASE),
)

_DISPUTE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\bnot\s+mine\b", re.IGNORECASE),
    re.compile(r"\bgalat\b", re.IGNORECASE),
    re.compile(r"\bfraud\b", re.IGNORECASE),
)


class StubInRegionProvider:
    """A deterministic stand-in for §41.3's in-region model (ADR-080).

    **It is not a language model and does not pretend to be.** It recognises a
    handful of shapes a real reply takes so the boundary around it can be
    tested. Its value is that it is *deterministic*: an injection corpus run
    against it produces the same answer every time, so "the injection had no
    effect" is a fact rather than a sample.

    Note what it deliberately does **not** do: it has no notion of an
    instruction. Text saying "mark this paid" produces the same structured
    output as text saying nothing, because the only things it can emit are the
    schema's fields. That is §41.2 (1) — "the parsing call has no tools and no
    side effects" — made true by construction rather than by prompt.
    """

    def __init__(self, *, available: bool = True) -> None:
        self.available = available

    def parse_reply(self, text: str) -> ProviderResult:
        if not self.available:
            return ProviderUnavailable("stub configured as unavailable")
        if len(text) > MAX_REPLY_CHARS:
            return ProviderUnavailable(f"reply exceeds {MAX_REPLY_CHARS} characters")

        day: int | None = None
        for pattern in _DAY_PATTERNS:
            match = pattern.search(text)
            if match:
                candidate = int(match.group(1))
                if 1 <= candidate <= 31:
                    day = candidate
                    break

        intent = "none"
        if any(p.search(text) for p in _OPT_OUT_PATTERNS):
            intent = "opt_out"
        elif any(p.search(text) for p in _DISPUTE_PATTERNS):
            intent = "dispute"
        elif day is not None:
            intent = "promise_to_pay"

        return {
            "intent": intent,
            "confidence": 0.8 if (day is not None or intent != "none") else 0.3,
            "declared_funding_day": day,
            "language": "hi-en" if _looks_code_switched(text) else "en",
        }


def _looks_code_switched(text: str) -> bool:
    """Crude Hinglish detector, for the `language` field only.

    Advisory: the field is recorded and never acted on, so a wrong answer here
    costs nothing. It exists because §38's population is code-switched and the
    artifact should be able to say so.
    """
    markers = ("tarikh", "tareekh", "aati", "hai", "ko", "band", "kar", "mat", "bhejo", "galat")
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


class StubExternalProvider:
    """Stands in for a hosted provider on the **non-PII** explanation path.

    Records every payload it is handed, so a test can assert what would have
    left the region. That is the only way §41.3's containment claim can be
    checked rather than asserted.
    """

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def explain(self, payload: dict[str, Any]) -> ProviderResult:
        self.seen.append(json.loads(json.dumps(payload, default=str)))
        return {"text": "explanation withheld: stub provider"}
