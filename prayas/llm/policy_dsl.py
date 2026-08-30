"""Merchant natural language to a *proposed* rule (Master Spec §30; ADR-082).

The Playbook asks for "merchant natural language → proposed rules, human
confirmation required", and the exit criterion is that "LLM-proposed rules
cannot activate without human confirmation".

**That guarantee is structural, not procedural.** A proposal is written to
`rule_proposals`. The compliance gate loads from `compliance_rules` and nothing
else. There is no function here that writes to `compliance_rules`, the app role
holds no grant on it, and `rule_proposals` has no `active` status to set —
its CHECK admits only `proposed` and `rejected`. Activation means a human
authoring a migration, which is the same path every other rule took.

The alternative — a row in `compliance_rules` with `active = false` — was
rejected in ADR-082 for the obvious reason: it is one missing WHERE clause in
the loader away from an LLM-authored rule going live, and Invariant 1 says no
code path debits without passing the gate. A gate that could be edited by a
model is not a gate.

**Every proposal carries its source text.** Invariant 10 requires a citation
and an `as_of` date on compliance rules; a proposal cannot supply a regulator's
citation, so it carries the sentence a merchant actually wrote instead. A
reviewer needs to see what was asked for, not only what was inferred.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

PROPOSED: Final = "proposed"
REJECTED: Final = "rejected"

#: The predicate vocabulary a proposal may use. Closed, and a subset of what
#: the gate's own predicates support — a proposal that could name anything the
#: gate can evaluate would be a proposal that could express anything a rule can.
ALLOWED_PREDICATES: Final[frozenset[str]] = frozenset(
    {"day_of_week", "hour_ist", "rail", "amount_paise_max", "attempts_per_day_max"}
)


class ProposalError(ValueError):
    """The proposal is not well formed, or is trying to be a rule."""


@dataclass(frozen=True, slots=True)
class RuleProposal:
    """A proposal. Inert: nothing here can reach the gate."""

    proposal_id: str
    tenant_id: str
    source_text: str
    predicate: str
    argument: Any
    proposed_at: datetime
    status: str = PROPOSED

    def __post_init__(self) -> None:
        if self.predicate not in ALLOWED_PREDICATES:
            raise ProposalError(
                f"unknown predicate {self.predicate!r}; expected one of "
                f"{sorted(ALLOWED_PREDICATES)}"
            )
        if self.status not in {PROPOSED, REJECTED}:
            raise ProposalError(
                f"status {self.status!r} is not writable here — activation is a "
                f"human-authored migration into compliance_rules (ADR-082)"
            )
        if not self.source_text.strip():
            raise ProposalError("a proposal must carry the sentence it came from")


async def record_proposal(conn: AsyncConnection, proposal: RuleProposal) -> None:
    """Persist a proposal. **The only write this module performs.**

    Note what is absent: there is no `activate`, no `promote`, and no statement
    naming `compliance_rules` anywhere in this file. The guarantee is that
    absence, not a check.
    """
    await conn.execute(
        text(
            "INSERT INTO rule_proposals (proposal_id, tenant_id, proposed_at,"
            " source_text, proposed_rule, status)"
            " VALUES (:pid, :t, :at, :src, CAST(:rule AS jsonb), :status)"
        ),
        {
            "pid": proposal.proposal_id,
            "t": proposal.tenant_id,
            "at": proposal.proposed_at,
            "src": proposal.source_text,
            "rule": json.dumps({"predicate": proposal.predicate, "argument": proposal.argument}),
            "status": proposal.status,
        },
    )


async def pending(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    """Proposals awaiting a human. Read-only, and for review only."""
    rows = await conn.execute(
        text(
            "SELECT proposal_id, source_text, proposed_rule, proposed_at"
            " FROM rule_proposals WHERE tenant_id = :t AND status = :s"
            " ORDER BY proposed_at"
        ),
        {"t": tenant_id, "s": PROPOSED},
    )
    return [dict(row._mapping) for row in rows]
