"""Canonical serialisation for the audit ledger (Master Spec §32).

Every record hash in every tenant chain is computed over the output of this
function. If its bytes ever change for the same logical input — a different key
order, a space after a separator, a different unicode escaping — every hash
computed before the change stops verifying, and the failure looks like tampering
rather than like a serialisation bug.

§32 states the parameters exactly: sorted keys, no whitespace, no ASCII
escaping. They are load-bearing, not stylistic:

* ``sort_keys=True`` — dict insertion order must not reach the hash.
* ``separators=(",", ":")`` — the default ``", "`` and ``": "`` inject spaces
  whose presence would have to be preserved forever.
* ``ensure_ascii=False`` — a Devanagari merchant name must hash identically
  whether or not the encoder felt like escaping it.
* ``default=str`` — datetimes and Decimals become strings deterministically
  instead of raising.

the ledger rules: "Any variance breaks chain verification in ways that
are painful to debug."
"""

from __future__ import annotations

import json
from typing import Any


def canonical(record: dict[str, Any]) -> bytes:
    """Deterministic serialisation. Never change these parameters."""
    return json.dumps(
        record,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
