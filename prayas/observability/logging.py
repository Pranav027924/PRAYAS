"""Structured logging with field-level PII redaction (Execution Playbook, Phase 0).

Stdlib only. Project standards forbid adding a dependency to avoid writing twenty lines,
and a JSON formatter plus a redaction filter is about that.

Redaction is deny-by-default on *key name*: a field whose name matches a sensitive
pattern is masked regardless of its value. Value-shaped scanning is a fallback for
free-text messages, not the primary mechanism, because pattern-matching values is
easy to evade and expensive to run on every record.

Invariant 7 means PAN and bank credentials should never reach a log line in the
first place. This is the second line of defence, not the first.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any, Final

MASK: Final = "[REDACTED]"

#: Field names that must never appear in plaintext. Matched case-insensitively
#: as a substring, so ``customer_email`` and ``EmailAddress`` both redact.
SENSITIVE_KEY_PARTS: Final[frozenset[str]] = frozenset(
    {
        # Invariant 7 — card and bank credentials
        "pan",
        "card_number",
        "cardnumber",
        "cvv",
        "expiry",
        "account_number",
        "accountnumber",
        "ifsc",
        "balance",
        # Contact PII — used by the notification path, never needed in logs
        "email",
        "phone",
        "mobile",
        "vpa",
        "upi_id",
        "address",
        # Credentials and signatures
        "password",
        "secret",
        "token",
        "authorization",
        "api_key",
        "apikey",
        "signature",
        "webhook_secret",
    }
)

#: Free-text fallbacks. Deliberately few — see module docstring.
_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\b\d{13,19}\b"),  # card-like digit runs
    re.compile(r"\b[\w.%-]+@[\w.-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b"),  # Indian mobile
)

_RESERVED: Final[frozenset[str]] = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}


def _is_sensitive(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Recursively mask sensitive fields in a structure.

    Depth-bounded: a cyclic or pathologically nested payload must not be able to
    stall the logging path, which sits in front of the money path.
    """
    if _depth > 6:
        return MASK
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, inner in value.items():
            key_str = str(key)
            out[key_str] = MASK if _is_sensitive(key_str) else redact(inner, _depth=_depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def _scrub_text(text: str) -> str:
    for pattern in _VALUE_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


class RedactingJsonFormatter(logging.Formatter):
    """One JSON object per line, with PII removed before it is emitted."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _scrub_text(record.getMessage()),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED:
                continue
            payload[key] = MASK if _is_sensitive(key) else redact(value)

        if record.exc_info:
            payload["exc"] = _scrub_text(self.formatException(record.exc_info))

        return json.dumps(payload, separators=(",", ":"), default=str)


def configure(level: str = "INFO") -> None:
    """Install the redacting formatter as the only root handler.

    Replaces existing handlers rather than adding to them, so a library that
    installed a plain handler cannot emit an unredacted duplicate line.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingJsonFormatter())

    root = logging.getLogger()
    for existing in root.handlers[:]:
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
