"""Decision replay (Master Spec §32).

"`GET /v1/decisions/{id}/replay` reconstructs from stored artifacts only:
trigger event, feature snapshot fetched by reference, cause posterior with
model version, both hazard curves, issuer health, **every candidate action with
its expected value including those not chosen**, compliance checks with
versions and citations, chosen action, propensity, arm, outcome, and chain
verification status."

"A reviewer clicks one recovered rupee and sees exactly why it happened."

Two properties make this a real guarantee rather than a formatted read:

* **Stored artifacts only.** Nothing is recomputed from live state. A replay
  that re-derived the verdict would show what the system thinks *now*, not what
  it decided then — and the whole point is to answer for a past action.
* **Chain-verified.** Every replay reports whether the record's hash still
  matches its contents and its link to the previous record. An unverified
  replay is a claim about a row that may have been altered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.ledger.canonical import canonical
from prayas.ledger.chain import HASHED_FIELDS, compute_hash


class ReplayError(RuntimeError):
    """The decision cannot be reconstructed."""


@dataclass(frozen=True, slots=True)
class Replay:
    """One decision, reconstructed from the ledger alone."""

    decision_id: str
    tenant_id: str
    chain_seq: int
    ts: datetime
    action_type: str
    verdict: str
    rationale: str | None
    candidate_actions: list[dict[str, Any]]
    chosen_action: dict[str, Any] | None
    compliance_checks: list[dict[str, Any]]
    holdout_arm: str | None
    propensity: float | None
    degraded: bool
    hash_matches: bool
    prev_hash: str
    record_hash: str

    @property
    def verified(self) -> bool:
        """Whether the stored hash still matches the stored contents."""
        return self.hash_matches

    @property
    def shows_rejected_candidates(self) -> bool:
        """§32 requires candidates *including those not chosen*.

        Recording only the winner makes a decision unreviewable: a reviewer
        cannot tell whether it beat a close second or an empty field.
        """
        return len(self.candidate_actions) > 0


def _as_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, str):
        value = json.loads(value)
    return list(value) if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else None


async def replay_decision(conn: AsyncConnection, decision_id: str) -> Replay:
    """Reconstruct one decision and verify its hash.

    Reads only `decisions`. Anything the reconstruction needs that is not in
    that row is, by §32's design, referenced from it — never recomputed.
    """
    columns = ", ".join([*HASHED_FIELDS, "record_hash"])
    row = (
        await conn.execute(
            # constant. The one caller-supplied value is a bound parameter.
            text(f"SELECT {columns} FROM decisions WHERE decision_id = :did"),  # nosec B608
            {"did": decision_id},
        )
    ).one_or_none()

    if row is None:
        raise ReplayError(f"no decision {decision_id!r} in the ledger")

    stored = dict(row._mapping)
    expected = compute_hash({field: stored[field] for field in HASHED_FIELDS})

    return Replay(
        decision_id=stored["decision_id"],
        tenant_id=stored["tenant_id"],
        chain_seq=int(stored["chain_seq"]),
        ts=stored["ts"],
        action_type=stored["action_type"],
        verdict=stored["verdict"],
        rationale=stored["rationale"],
        candidate_actions=_as_list(stored["candidate_actions"]),
        chosen_action=_as_dict(stored["chosen_action"]),
        compliance_checks=_as_list(stored["compliance_checks"]),
        holdout_arm=stored["holdout_arm"],
        propensity=None if stored["propensity"] is None else float(stored["propensity"]),
        degraded=bool(stored["degraded"]),
        hash_matches=expected == stored["record_hash"],
        prev_hash=stored["prev_hash"],
        record_hash=stored["record_hash"],
    )


async def replay_all(conn: AsyncConnection, tenant_id: str) -> list[Replay]:
    """Replay every decision for a tenant, in chain order.

    Phase 8's criterion is "every decision replayable", so the check has to be
    exhaustive rather than a sample — a sample would not detect the one record
    that fails.
    """
    ids = [
        r.decision_id
        for r in await conn.execute(
            text("SELECT decision_id FROM decisions WHERE tenant_id = :tid ORDER BY chain_seq"),
            {"tid": tenant_id},
        )
    ]
    return [await replay_decision(conn, decision_id) for decision_id in ids]


def canonical_bytes(replay: Replay) -> bytes:
    """The record's canonical serialisation, for external checkpointing (§32)."""
    return canonical(
        {
            "decision_id": replay.decision_id,
            "tenant_id": replay.tenant_id,
            "chain_seq": replay.chain_seq,
            "record_hash": replay.record_hash,
        }
    )
