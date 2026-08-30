"""The declared output schema for reply parsing (Master Spec §41.2).

§41.2 states the governing principle: **"treat every LLM output as untrusted
data with a declared schema and a capped blast radius, exactly as you would
treat a form field submitted by a stranger."**

So this module is deliberately unexciting. It is a form validator. It knows
nothing about models, prompts, or providers — it takes a decoded object of
unknown provenance and either returns a value every field of which has been
checked, or returns a rejection. There is no third outcome.

**Validation is atomic, and that is the whole design.** §41.2's second
mitigation is that "anything unparseable is discarded" — discarded, not
salvaged. A validator that accepted `declared_funding_day` while rejecting
`intent` would be doing exactly what an attacker wants: taking the part of the
payload that survived scrutiny. Every field is checked before any field is
used, and the result is a single immutable value or nothing at all.

**The intent vocabulary is closed.** An unrecognised intent is a rejection, not
a fallback to `none`. Silently mapping unknown values onto a safe default would
mean a model that started emitting a new intent tomorrow would be *ignored*
rather than noticed, and the human queue exists precisely to notice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from prayas.memory.profile import DAYS_IN_MONTH

#: §41.2 (4) — "`intent: opt_out` is the one high-impact output".
OPT_OUT: Final = "opt_out"
NONE: Final = "none"
PROMISE_TO_PAY: Final = "promise_to_pay"
DISPUTE: Final = "dispute"
QUESTION: Final = "question"

#: Closed vocabulary. Anything else is a rejection, never a coerced default.
INTENTS: Final[frozenset[str]] = frozenset({NONE, OPT_OUT, PROMISE_TO_PAY, DISPUTE, QUESTION})

#: The only keys permitted. An unexpected key is a rejection: it means the
#: producer and this schema disagree about the contract, and guessing which of
#: them is right is not a decision a validator should make silently.
FIELDS: Final[frozenset[str]] = frozenset(
    {"declared_funding_day", "intent", "confidence", "language"}
)

REQUIRED: Final[frozenset[str]] = frozenset({"intent", "confidence"})

#: Advisory only — recorded, never acted on. §38's population is code-switched
#: and a parser that refused unfamiliar language tags would reject real replies.
MAX_LANGUAGE_LENGTH: Final = 32


@dataclass(frozen=True, slots=True)
class ParsedReply:
    """A reply that passed every check. Immutable, so it cannot be topped up."""

    intent: str
    confidence: float
    declared_funding_day: int | None = None
    language: str | None = None

    @property
    def is_opt_out(self) -> bool:
        return self.intent == OPT_OUT

    @property
    def has_funding_hint(self) -> bool:
        return self.declared_funding_day is not None


@dataclass(frozen=True, slots=True)
class Rejection:
    """Why a payload was discarded, in terms a human queue can act on."""

    reason: str
    field: str | None = None

    def __str__(self) -> str:
        return f"{self.field}: {self.reason}" if self.field else self.reason


#: The result of validation. A caller must handle both, and cannot get at a
#: half-built `ParsedReply` because one is never constructed on the reject path.
ValidationResult = ParsedReply | Rejection


def _reject(reason: str, field: str | None = None) -> Rejection:
    return Rejection(reason=reason, field=field)


def validate(payload: Any) -> ValidationResult:
    """Check an untrusted payload against the declared schema.

    Returns `ParsedReply` only if **every** field is valid. Any problem yields
    a `Rejection` and nothing is constructed — §41.2's "discarded, never
    partially applied", enforced by there being no partial object to apply.
    """
    if not isinstance(payload, dict):
        return _reject(f"expected an object, got {type(payload).__name__}")

    unexpected = set(payload) - FIELDS
    if unexpected:
        # Not merely strict for its own sake: an unexpected key means the
        # producer and this schema disagree about the contract, and a payload
        # carrying instructions would arrive exactly this way.
        return _reject(f"unexpected keys: {sorted(unexpected)}")

    missing = REQUIRED - set(payload)
    if missing:
        return _reject(f"missing required keys: {sorted(missing)}")

    intent = payload["intent"]
    if not isinstance(intent, str):
        return _reject(f"expected a string, got {type(intent).__name__}", "intent")
    if intent not in INTENTS:
        return _reject(f"unknown intent {intent!r}; expected one of {sorted(INTENTS)}", "intent")

    confidence = payload["confidence"]
    # bool is a subclass of int; `True` is not a confidence.
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        return _reject(f"expected a number, got {type(confidence).__name__}", "confidence")
    if not 0.0 <= float(confidence) <= 1.0:
        return _reject(f"must be in [0, 1], got {confidence}", "confidence")

    day = payload.get("declared_funding_day")
    if day is not None:
        if isinstance(day, bool) or not isinstance(day, int):
            return _reject(f"expected an integer, got {type(day).__name__}", "declared_funding_day")
        if not 1 <= day <= DAYS_IN_MONTH:
            return _reject(f"must be in [1, {DAYS_IN_MONTH}], got {day}", "declared_funding_day")

    language = payload.get("language")
    if language is not None:
        if not isinstance(language, str):
            return _reject(f"expected a string, got {type(language).__name__}", "language")
        if not language or len(language) > MAX_LANGUAGE_LENGTH:
            return _reject(
                f"must be 1 to {MAX_LANGUAGE_LENGTH} characters, got {len(language)}", "language"
            )

    return ParsedReply(
        intent=intent,
        confidence=float(confidence),
        declared_funding_day=day,
        language=language,
    )
