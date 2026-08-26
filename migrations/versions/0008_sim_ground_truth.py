"""Simulator ground truth (ADR-030; Master Spec §38).

"Each cycle emits `true_cause`, `true_funding_time`, and `issuer_state`
alongside the observable stream. **Models see only the observables.**"

That last sentence is enforced here rather than by convention: `prayas_app` is
granted **nothing** on this table. A feature query cannot join a label it has no
privilege to read, so training-serving leakage becomes impossible rather than
merely discouraged — the same move ADR-004 used to make tenant isolation real.

Evaluation code (§20's confusion matrix) reads it through the owner role.

RLS is still enabled: the table carries `tenant_id`, and the isolation metatest
requires every such table to be policed. The owner-scoped policy mirrors
`webhook_secrets` (ADR-014a) so a SECURITY DEFINER-style read stays possible
under FORCE.

Revision ID: 0008_sim_ground_truth
Revises: 0007_load_rulepack
Create Date: 2026-08-26

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0008_sim_ground_truth"
down_revision: str | None = "0007_load_rulepack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"
OWNER_ROLE = "prayas_owner"
TENANT_EXPR = "current_setting('app.tenant_id', true)"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE sim_ground_truth (
            tenant_id         TEXT NOT NULL REFERENCES tenants(tenant_id),
            cycle_id          TEXT NOT NULL,
            run_id            TEXT NOT NULL,   -- one simulator run, for reproducibility
            seed              BIGINT NOT NULL,
            true_cause        TEXT NOT NULL,   -- §20 latent cause, never observable
            true_funding_time TIMESTAMPTZ,     -- NULL = never funded in the horizon
            issuer_state      TEXT NOT NULL,   -- healthy | degraded
            payday_archetype  TEXT NOT NULL,
            masked_as_05      BOOLEAN NOT NULL DEFAULT false,
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, cycle_id)
        );
        """
    )
    op.execute("CREATE INDEX idx_sim_truth_run ON sim_ground_truth (run_id, tenant_id);")

    op.execute("ALTER TABLE sim_ground_truth ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE sim_ground_truth FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY tenant_isolation ON sim_ground_truth"
        f" USING (tenant_id = {TENANT_EXPR})"
        f" WITH CHECK (tenant_id = {TENANT_EXPR});"
    )
    op.execute(
        f"CREATE POLICY owner_evaluation ON sim_ground_truth"
        f" FOR SELECT TO {OWNER_ROLE} USING (true);"
    )

    # Deliberately no GRANT to prayas_app. Stated explicitly so that a future
    # migration adding one has to argue with this comment first.
    op.execute(f"REVOKE ALL ON sim_ground_truth FROM {APP_ROLE};")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS owner_evaluation ON sim_ground_truth;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON sim_ground_truth;")
    op.execute("DROP TABLE IF EXISTS sim_ground_truth CASCADE;")
