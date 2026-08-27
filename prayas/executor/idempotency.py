"""Deterministic idempotency keys and jitter (Master Spec §31; ADR-045).

Invariant 4: every external side effect carries a deterministic idempotency key.
"Deterministic, so a logical retry always produces the same key. Amount is
included so a mid-cycle amount change cannot silently reuse one."

The key is the attempt's identity. Jitter is derived from it (ADR-045) so a
retry of the same logical attempt lands at the same instant rather than
drifting, and §32's replay can reconstruct the fire time from stored artifacts
alone — which §40.9's replay determinism requires.
"""

from __future__ import annotations

import hashlib
from typing import Final

#: §31's prefix and digest length, verbatim.
KEY_PREFIX: Final = "prayas_"
KEY_DIGEST_CHARS: Final = 32


def idem_key(cycle_id: str, attempt_seq: int, action_type: str, amount_paise: int) -> str:
    """§31's derivation, transcribed exactly.

        raw = f"{cycle_id}:{attempt_seq}:{action_type}:{amount_paise}"
        return "prayas_" + sha256(raw).hexdigest()[:32]

    Amount is part of the key on purpose: if a mandate's amount changes
    mid-cycle, the next attempt is a *different* external effect and must not
    inherit the previous key's idempotency.
    """
    if not isinstance(amount_paise, int):
        raise TypeError("amount_paise must be an int - money is integer paise")
    if amount_paise < 0:
        raise ValueError(f"amount_paise must be non-negative, got {amount_paise}")
    if attempt_seq < 0:
        raise ValueError(f"attempt_seq must be non-negative, got {attempt_seq}")
    if not cycle_id or not action_type:
        raise ValueError("cycle_id and action_type must be non-empty")

    raw = f"{cycle_id}:{attempt_seq}:{action_type}:{amount_paise}"
    return KEY_PREFIX + hashlib.sha256(raw.encode()).hexdigest()[:KEY_DIGEST_CHARS]


def jitter_seconds(key: str, *, spread_seconds: int) -> int:
    """Deterministic offset within a slot, derived from the idempotency key.

    ADR-045. Uniform over `[0, spread_seconds)` and stable for a given key, so
    the same logical attempt always lands at the same instant. Spreading a
    merchant's month-start burst across the slot is the point; doing it
    reproducibly is what keeps replay exact.

    Hashed again rather than slicing the key directly: the key's own hex is
    already a digest, but taking an int from a prefix of it would correlate
    jitter with any other use of that prefix.
    """
    if spread_seconds <= 0:
        raise ValueError(f"spread_seconds must be positive, got {spread_seconds}")
    digest = hashlib.sha256(f"jitter:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % spread_seconds
