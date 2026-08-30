"""Hash-chained audit ledger (Master Spec §32; Invariant 5).

Per-tenant chains, so writes parallelise while staying tamper-evident, and so
one merchant's volume cannot bottleneck another's audit trail.

**Append-only is enforced by privilege, not by convention.** `prayas_app` holds
INSERT and SELECT on `decisions` and nothing else (ADR-004). This module could
not UPDATE the ledger if it tried.

**Why the chain matters more here than in §32's sketch.** ADR-005 made the
unique constraint `(tenant_id, chain_seq, ts)` because PostgreSQL requires the
partition key in every unique index — so `(tenant_id, chain_seq)` is now unique
only *within* a partition. The database will no longer catch a duplicate
sequence number spanning a month boundary. The verifier is what catches it.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.ledger.canonical import canonical

log = logging.getLogger(__name__)

#: `prev_hash` of the first record in a chain (ADR-024).
#: §32 references GENESIS without defining it. Any fixed value works; changing
#: it would invalidate every chain ever written, so it never changes.
GENESIS: Final = "0" * 64

#: Fields that participate in the hash. Anything outside this set can be
#: modified without breaking the chain, so the set must cover every field a
#: reviewer would rely on. Outcome fields are included: a decision whose result
#: was quietly rewritten is exactly the tampering §32 exists to detect.
HASHED_FIELDS: Final[tuple[str, ...]] = (
    "decision_id",
    "tenant_id",
    "chain_seq",
    "prev_hash",
    "ts",
    "trigger_event_id",
    "mandate_id",
    "cycle_id",
    "action_type",
    "verdict",
    "feature_snapshot_ref",
    "model_versions",
    "cause_posterior",
    "liquidity_curve_ref",
    "revocation_hazard",
    "continuation_value",
    "candidate_actions",
    "chosen_action",
    "rationale",
    "compliance_checks",
    "holdout_arm",
    "propensity",
    "degraded",
    "outcome",
    "outcome_ts",
    "recovered_paise",
)


class ChainError(RuntimeError):
    """The chain is broken, or an append would break it."""


@dataclass(frozen=True, slots=True)
class ChainBreak:
    """One detected discrepancy, located precisely enough to investigate."""

    tenant_id: str
    chain_seq: int
    decision_id: str
    reason: str
    expected: str
    found: str

    def __str__(self) -> str:
        return (
            f"tenant={self.tenant_id} seq={self.chain_seq} decision={self.decision_id} "
            f"{self.reason}: expected {self.expected}, found {self.found}"
        )


#: Columns declared JSONB. PostgreSQL returns these as decoded Python objects,
#: while a caller naturally supplies them as JSON text — so the same logical
#: record would hash two different ways depending on which side you were on.
JSON_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "model_versions",
        "cause_posterior",
        "candidate_actions",
        "chosen_action",
        "compliance_checks",
    }
)


def _normalise(field: str, value: Any) -> Any:
    """Reduce a value to one representation, whichever side it arrived from.

    Two round-trips would otherwise change the bytes without anyone editing the
    record, and a chain that fails to verify for a serialisation reason is
    indistinguishable from one that fails because it was tampered with:

    * JSONB — text on the way in, `dict`/`list` on the way out.
    * NUMERIC — `float` on the way in, `Decimal("0.50000")` on the way out,
      whose `str()` carries the column scale and so differs from `str(0.5)`.
    """
    if field in JSON_FIELDS and isinstance(value, str | bytes):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return value
    if isinstance(value, Decimal):
        return float(value)
    return value


def compute_hash(body: dict[str, Any]) -> str:
    """Hash of a record body, over the canonical form of the hashed fields only."""
    subset = {field: _normalise(field, body.get(field)) for field in HASHED_FIELDS}
    return hashlib.sha256(canonical(subset)).hexdigest()


async def append(conn: AsyncConnection, record: dict[str, Any], tenant_id: str) -> str:
    """Append one record to a tenant's chain. Returns its `record_hash`.

    Must run inside a transaction. The advisory lock is transaction-scoped, so
    concurrent appends for one tenant serialise, while different tenants proceed
    in parallel.

    `hashtext` is required because `pg_advisory_xact_lock` takes a bigint and
    `tenant_id` is TEXT (§32 uses it for the same reason). It is an undocumented
    internal function with no collision guarantee — two tenants whose ids hash
    equal would serialise against each other. That costs throughput, never
    correctness, and the value is never persisted.
    """
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:tenant_id))"), {"tenant_id": tenant_id}
    )

    head = (
        await conn.execute(
            text(
                "SELECT chain_seq, record_hash FROM decisions"
                " WHERE tenant_id = :tenant_id"
                " ORDER BY chain_seq DESC LIMIT 1"
            ),
            {"tenant_id": tenant_id},
        )
    ).one_or_none()

    body: dict[str, Any] = {
        **record,
        "tenant_id": tenant_id,
        "chain_seq": (head.chain_seq + 1) if head else 0,
        "prev_hash": head.record_hash if head else GENESIS,
    }
    body["record_hash"] = compute_hash(body)

    # Column *names* below are interpolated from `HASHED_FIELDS`, a module
    # constant; every column *value* is a bound parameter. Building the list
    # dynamically is what keeps the INSERT and the hash input provably the same
    # set of fields — writing them out twice is how the two drift apart.
    columns = [*HASHED_FIELDS, "record_hash"]
    await conn.execute(
        text(
            f"INSERT INTO decisions ({', '.join(columns)})"  # nosec B608
            f" VALUES ({', '.join(':' + c for c in columns)})"
        ),
        {column: body.get(column) for column in columns},
    )

    return str(body["record_hash"])


async def verify_chain(conn: AsyncConnection, tenant_id: str) -> list[ChainBreak]:
    """Walk a tenant's chain and report every discrepancy.

    Returns all breaks rather than the first, so a single pass tells an operator
    the full extent of the damage. Three things are checked per record:
    the recomputed hash, the link to the previous record, and sequence
    contiguity — the last because ADR-005 removed the database's ability to
    enforce it across partitions.
    """
    rows = (
        await conn.execute(
            text(
                f"SELECT {', '.join([*HASHED_FIELDS, 'record_hash'])} FROM decisions"  # nosec B608
                " WHERE tenant_id = :tenant_id ORDER BY chain_seq"
            ),
            {"tenant_id": tenant_id},
        )
    ).mappings()

    breaks: list[ChainBreak] = []
    expected_prev = GENESIS
    expected_seq = 0

    for row in rows:
        body = dict(row)
        stored_hash = str(body.pop("record_hash"))
        decision_id = str(body.get("decision_id"))
        chain_seq = int(body["chain_seq"])

        if chain_seq != expected_seq:
            breaks.append(
                ChainBreak(
                    tenant_id,
                    chain_seq,
                    decision_id,
                    "sequence gap",
                    str(expected_seq),
                    str(chain_seq),
                )
            )

        if str(body.get("prev_hash")) != expected_prev:
            breaks.append(
                ChainBreak(
                    tenant_id,
                    chain_seq,
                    decision_id,
                    "broken link",
                    expected_prev,
                    str(body.get("prev_hash")),
                )
            )

        recomputed = compute_hash(body)
        if recomputed != stored_hash:
            breaks.append(
                ChainBreak(
                    tenant_id,
                    chain_seq,
                    decision_id,
                    "hash mismatch",
                    recomputed,
                    stored_hash,
                )
            )

        # Continue from what is *stored*, so one tampered record produces one
        # break rather than cascading a mismatch through every later record.
        expected_prev = stored_hash
        expected_seq = chain_seq + 1

    return breaks
