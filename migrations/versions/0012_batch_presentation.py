"""Batch presentation state for eNACH (Master Spec §9; ADR-083).

§9: "eNACH is the hardest rail and therefore the one that proves the
abstraction. Batch semantics mean 'attempt at 09:10 on the 5th' is meaningless;
you present into a clearing cycle and learn the outcome T+1."

`attempts` could already express *that* an outcome was unknown — `state` holds
`fired` — but not *when* one is due. Without that, "is this attempt overdue?"
has no answer, and an outcome that never arrives is indistinguishable from one
that has not arrived yet. On a rail with a one-working-day latency and a
weekend, those are days apart.

Both columns are nullable: the real-time rails present and resolve in the same
instant, and writing `presented_at = fired_at` for them would imply a
distinction that does not exist there.

**Recorded rather than computed.** The due time could be derived from
`fired_at` plus the adapter's latency, but §32 replay reconstructs decisions
from stored artifacts, and a due time recomputed later against a changed
clearing calendar would silently disagree with what the system actually
expected at the time.

Revision ID: 0012_batch_presentation
Revises: 0011_llm_layer
Create Date: 2026-08-30

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0012_batch_presentation"
down_revision: str | None = "0011_llm_layer"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE attempts ADD COLUMN presented_at TIMESTAMPTZ;")
    op.execute("ALTER TABLE attempts ADD COLUMN outcome_due_at TIMESTAMPTZ;")
    op.execute(
        "COMMENT ON COLUMN attempts.outcome_due_at IS "
        "'When a batch outcome should have arrived. NULL on real-time rails.';"
    )
    # The overdue sweep is the only query these support, so it is the only
    # index worth carrying.
    op.execute(
        "CREATE INDEX attempts_awaiting_outcome ON attempts (tenant_id, outcome_due_at)"
        " WHERE outcome_due_at IS NOT NULL AND state = 'fired';"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS attempts_awaiting_outcome;")
    op.execute("ALTER TABLE attempts DROP COLUMN IF EXISTS outcome_due_at;")
    op.execute("ALTER TABLE attempts DROP COLUMN IF EXISTS presented_at;")
