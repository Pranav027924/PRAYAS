"""Inbound reply queue and inert rule proposals (Master Spec §41.2, §30; ADR-081).

Two tables, deliberately separate.

`inbound_replies` is §41.2's human queue: "anything unparseable is discarded
and the reply routed to a human queue". It holds the raw text, because a person
triaging a parse failure needs to see what actually arrived — which makes it a
**personal-data store**, tenant-scoped and subject to §28's forgetting.

`rule_proposals` holds what the policy DSL produced from a merchant's natural
language. It is **inert by construction**: the compliance gate loads from
`compliance_rules` and nothing else, and there is no code path — and no grant —
that moves a row from here to there. Activation is a human-authored migration.
That is ADR-082's whole point: "cannot activate without human confirmation"
should be a property of the schema, not a promise about a code review.

The app role gets `SELECT, INSERT` on proposals and no UPDATE or DELETE. A
proposal that could be edited after review is a proposal that was not reviewed.

Revision ID: 0011_llm_layer
Revises: 0010_contact_suppressions
Create Date: 2026-08-30

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0011_llm_layer"
down_revision: str | None = "0010_contact_suppressions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "prayas_app"
POLICY = "tenant_isolation"
TENANT_EXPR = "current_setting('app.tenant_id', true)"

TABLES = ("inbound_replies", "rule_proposals")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE inbound_replies (
            reply_id      TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id),
            customer_ref  TEXT NOT NULL,
            received_at   TIMESTAMPTZ NOT NULL,
            raw_text      TEXT NOT NULL,
            parsed        JSONB,
            rejection     TEXT,
            needs_human   BOOLEAN NOT NULL DEFAULT false,
            resolved_at   TIMESTAMPTZ
        );
        """
    )
    op.execute(
        "COMMENT ON COLUMN inbound_replies.raw_text IS "
        "'Customer-authored personal data. Subject to Sec 28 forgetting.';"
    )
    op.execute(
        "CREATE INDEX inbound_replies_queue ON inbound_replies (tenant_id, received_at)"
        " WHERE needs_human AND resolved_at IS NULL;"
    )

    op.execute(
        """
        CREATE TABLE rule_proposals (
            proposal_id   TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id),
            proposed_at   TIMESTAMPTZ NOT NULL,
            source_text   TEXT NOT NULL,
            proposed_rule JSONB NOT NULL,
            citation      TEXT,
            status        TEXT NOT NULL DEFAULT 'proposed'
                          CHECK (status IN ('proposed', 'rejected'))
        );
        """
    )
    # There is no 'active' status, and that is the enforcement. A proposal
    # cannot be promoted in place; activation means a human writing a migration
    # against `compliance_rules`, which this role cannot write to at all.
    op.execute(
        "COMMENT ON TABLE rule_proposals IS "
        "'Inert. The gate loads from compliance_rules only; nothing promotes a row from here.';"
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {POLICY} ON {table}"
            f" USING (tenant_id = {TENANT_EXPR})"
            f" WITH CHECK (tenant_id = {TENANT_EXPR});"
        )

    # The queue is worked: a human marks a reply resolved.
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON inbound_replies TO {APP_ROLE};")
    # Proposals are not. A proposal that can be edited after review is a
    # proposal that was not reviewed.
    op.execute(f"GRANT SELECT, INSERT ON rule_proposals TO {APP_ROLE};")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"REVOKE ALL ON {table} FROM {APP_ROLE};")
        op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table};")
        op.execute(f"DROP TABLE IF EXISTS {table};")
