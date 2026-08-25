"""Full schema per Master Spec §36.

Transcribed faithfully except where an ADR approved a change. Every deviation is
marked inline with its ADR number:

* ADR-005 — ``decisions`` PRIMARY KEY and UNIQUE extended to include ``ts``.
  §36 as written cannot be created: PostgreSQL requires every unique constraint
  on a partitioned table to include all partition key columns.
* ADR-006 — monthly partitions plus a DEFAULT partition. §36 declares
  ``PARTITION BY RANGE (ts)`` but defines no partitions, so the first INSERT
  would fail with "no partition of relation found".
* ADR-008 — ``REFERENCES tenants(tenant_id)`` added on seven tables. §36 carries
  it only on ``mandates``. ``events_raw`` is deliberately excluded: it is the raw
  landing zone and a webhook for an unprovisioned tenant must land and be
  flagged, not be rejected.

Revision ID: 0002_schema
Revises: 0001_app_role
Create Date: 2026-08-24

"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from alembic import op

revision: str = "0002_schema"
down_revision: str | None = "0001_app_role"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Deterministic partition window. Not derived from "now" — a migration whose
#: output depends on when it ran is not reproducible, and §32 requires replay.
PARTITION_START_YEAR = 2026
PARTITION_END_YEAR = 2027


def _monthly_ranges() -> list[tuple[str, str, str]]:
    ranges: list[tuple[str, str, str]] = []
    for year in range(PARTITION_START_YEAR, PARTITION_END_YEAR + 1):
        for month in range(1, 13):
            start = date(year, month, 1)
            end = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            ranges.append((f"decisions_{year}{month:02d}", start.isoformat(), end.isoformat()))
    return ranges


def upgrade() -> None:
    # ═══════════ TENANCY ═══════════
    op.execute(
        """
        CREATE TABLE tenants (
            tenant_id       TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            adoption_stage  TEXT NOT NULL DEFAULT 'observe',   -- observe|shadow|canary|ramp|full
            config          JSONB NOT NULL DEFAULT '{}',       -- lambda, mu, caps, rails, kill switches
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    # ═══════════ EVENT SPINE ═══════════
    # No FK on tenant_id (ADR-008): evidence must survive an onboarding race.
    op.execute(
        """
        CREATE TABLE events_raw (
            event_id     TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL,
            event_type   TEXT NOT NULL,
            mandate_id   TEXT,
            payload      JSONB NOT NULL,
            signature_ok BOOLEAN NOT NULL,
            received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            processed_at TIMESTAMPTZ
        );
        """
    )
    # asyncpg prepares every statement, and a prepared statement may hold only
    # one command — so each DDL statement is issued separately throughout.
    op.execute("CREATE INDEX idx_events_mandate ON events_raw (mandate_id, received_at);")

    # ═══════════ DOMAIN ═══════════
    op.execute(
        """
        CREATE TABLE mandates (
            mandate_id       TEXT PRIMARY KEY,
            tenant_id        TEXT NOT NULL REFERENCES tenants(tenant_id),
            customer_id      TEXT NOT NULL,
            rail             TEXT NOT NULL CHECK (rail IN ('upi_autopay','card_emandate','enach')),
            issuer_code      TEXT,
            mcc              TEXT,
            debit_day        SMALLINT,
            max_amount_paise BIGINT NOT NULL,
            state            TEXT NOT NULL,
            consent_ref      TEXT NOT NULL,
            tenure_cycles    INT NOT NULL DEFAULT 0,
            revocation_risk  NUMERIC(5,4),
            continuation_value_paise BIGINT,
            created_at       TIMESTAMPTZ NOT NULL,
            version          INT NOT NULL DEFAULT 0
        );
        """
    )
    op.execute(
        "CREATE INDEX idx_mandates_at_risk ON mandates (tenant_id, revocation_risk DESC)"
        " WHERE state IN ('active','at_risk');"
    )

    op.execute(
        """
        CREATE TABLE cycles (
            cycle_id        TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),   -- ADR-008
            mandate_id      TEXT NOT NULL REFERENCES mandates(mandate_id),
            seq_no          INT NOT NULL,
            amount_paise    BIGINT NOT NULL,
            due_at          TIMESTAMPTZ NOT NULL,
            deadline_at     TIMESTAMPTZ NOT NULL,
            attempt_budget  SMALLINT NOT NULL,
            attempts_used   SMALLINT NOT NULL DEFAULT 0,
            state           TEXT NOT NULL,
            pdn_sent_at     TIMESTAMPTZ,
            last_failure_at TIMESTAMPTZ,
            recovered_paise BIGINT DEFAULT 0,
            version         INT NOT NULL DEFAULT 0,
            UNIQUE (mandate_id, seq_no),
            CHECK (attempts_used <= attempt_budget)   -- the regulator's cap, in the schema
        );
        """
    )

    op.execute(
        """
        CREATE TABLE attempts (
            attempt_id    TEXT PRIMARY KEY,
            tenant_id     TEXT NOT NULL REFERENCES tenants(tenant_id),   -- ADR-008
            cycle_id      TEXT NOT NULL REFERENCES cycles(cycle_id),
            attempt_seq   SMALLINT NOT NULL,
            idem_key      TEXT NOT NULL UNIQUE,   -- Invariant 4
            scheduled_for TIMESTAMPTZ NOT NULL,
            fired_at      TIMESTAMPTZ,
            state         TEXT NOT NULL,
            decline_code  TEXT,
            provider_ref  TEXT,
            decision_id   TEXT NOT NULL,          -- unenforced reference (ADR-008)
            UNIQUE (cycle_id, attempt_seq)
        );
        """
    )

    op.execute(
        """
        CREATE TABLE interventions (
            intervention_id TEXT PRIMARY KEY,
            tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id),   -- ADR-008
            mandate_id      TEXT NOT NULL,
            kind            TEXT NOT NULL,     -- date_change|amount_adj|rail_migration|partial|backoff
            proposed_at     TIMESTAMPTZ NOT NULL,
            accepted_at     TIMESTAMPTZ,
            payload         JSONB NOT NULL,
            decision_id     TEXT NOT NULL      -- unenforced reference (ADR-008)
        );
        """
    )

    # ═══════════ MEMORY ═══════════
    op.execute(
        """
        CREATE TABLE customer_profiles (
            tenant_id            TEXT NOT NULL REFERENCES tenants(tenant_id),   -- ADR-008
            customer_id          TEXT NOT NULL,
            payday_posterior     JSONB NOT NULL DEFAULT '{}',
            payday_confidence    NUMERIC(4,3),
            declared_funding_day SMALLINT,
            declared_at          TIMESTAMPTZ,
            typical_amount_p75   BIGINT,
            fail_success_lags    NUMERIC[] DEFAULT '{}',
            preferred_channel    TEXT,
            engagement_hours     SMALLINT[],
            messages_30d         SMALLINT NOT NULL DEFAULT 0,
            fatigue_score        NUMERIC(4,3) NOT NULL DEFAULT 0,
            last_contact_at      TIMESTAMPTZ,
            consent_ref          TEXT,
            consent_withdrawn    BOOLEAN NOT NULL DEFAULT false,
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, customer_id)
        );
        """
    )

    # Cross-tenant by design: no PII, k-anonymised (§27, Invariant 8).
    op.execute(
        """
        CREATE TABLE segment_priors (
            mcc          TEXT NOT NULL,
            ticket_band  TEXT NOT NULL,
            rail         TEXT NOT NULL,
            day_of_month SMALLINT NOT NULL,
            hour_band    SMALLINT NOT NULL,
            hazard       NUMERIC(6,5) NOT NULL,
            n_obs        INT NOT NULL,
            updated_at   TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (mcc, ticket_band, rail, day_of_month, hour_band),
            CHECK (n_obs >= 50)                    -- minimum cohort size
        );
        """
    )

    # ═══════════ COMPLIANCE ═══════════
    op.execute(
        """
        CREATE TABLE compliance_rules (
            rule_id    TEXT NOT NULL,
            version    INT  NOT NULL,
            regulator  TEXT NOT NULL,
            citation   TEXT NOT NULL,      -- Invariant 10
            as_of      DATE NOT NULL,      -- Invariant 10
            applies_to TEXT[] NOT NULL,
            rails      TEXT[],
            predicate  TEXT NOT NULL,
            on_fail    TEXT NOT NULL CHECK (on_fail IN ('DENY','DEFER','ESCALATE_HUMAN')),
            active     BOOLEAN NOT NULL DEFAULT true,
            created_by TEXT NOT NULL,
            PRIMARY KEY (rule_id, version)
        );
        """
    )

    # ═══════════ LEDGER ═══════════
    # ADR-005: PK and UNIQUE both extended with ts, the partition key.
    op.execute(
        """
        CREATE TABLE decisions (
            decision_id          TEXT NOT NULL,
            tenant_id            TEXT NOT NULL REFERENCES tenants(tenant_id),   -- ADR-008
            chain_seq            BIGINT NOT NULL,
            prev_hash            TEXT NOT NULL,
            record_hash          TEXT NOT NULL,
            ts                   TIMESTAMPTZ NOT NULL,
            trigger_event_id     TEXT,
            mandate_id           TEXT,
            cycle_id             TEXT,
            action_type          TEXT NOT NULL,
            verdict              TEXT NOT NULL,
            feature_snapshot_ref TEXT,          -- reference, never inline features
            model_versions       JSONB NOT NULL,
            cause_posterior      JSONB,
            liquidity_curve_ref  TEXT,
            revocation_hazard    NUMERIC(6,5),
            continuation_value   BIGINT,
            candidate_actions    JSONB NOT NULL,
            chosen_action        JSONB,
            rationale            TEXT,
            compliance_checks    JSONB NOT NULL,
            holdout_arm          TEXT,
            propensity           NUMERIC(6,5),
            degraded             BOOLEAN NOT NULL DEFAULT false,
            outcome              TEXT,
            outcome_ts           TIMESTAMPTZ,
            recovered_paise      BIGINT,
            PRIMARY KEY (decision_id, ts),        -- ADR-005
            UNIQUE (tenant_id, chain_seq, ts)     -- ADR-005
        ) PARTITION BY RANGE (ts);
        """
    )

    # ADR-006: deterministic monthly partitions plus a DEFAULT safety net.
    for name, start, end in _monthly_ranges():
        op.execute(
            f"CREATE TABLE {name} PARTITION OF decisions FOR VALUES FROM ('{start}') TO ('{end}');"
        )

    # An audit write must never be lost to a missing range. Rows landing here
    # block later creation of an overlapping partition, so this must alert.
    op.execute("CREATE TABLE decisions_default PARTITION OF decisions DEFAULT;")

    # ═══════════ EXECUTION ═══════════
    op.execute(
        """
        CREATE TABLE scheduled_actions (
            action_id    TEXT PRIMARY KEY,
            tenant_id    TEXT NOT NULL REFERENCES tenants(tenant_id),   -- ADR-008
            cycle_id     TEXT,
            mandate_id   TEXT,
            action_type  TEXT NOT NULL,
            fire_at      TIMESTAMPTZ NOT NULL,
            state        TEXT NOT NULL DEFAULT 'pending',
            locked_until TIMESTAMPTZ,
            payload      JSONB NOT NULL
        );
        """
    )
    op.execute("CREATE INDEX idx_sched_due ON scheduled_actions (fire_at) WHERE state = 'pending';")

    op.execute(
        """
        CREATE TABLE outbox (
            outbox_id  BIGSERIAL PRIMARY KEY,
            tenant_id  TEXT NOT NULL REFERENCES tenants(tenant_id),   -- ADR-008
            idem_key   TEXT NOT NULL UNIQUE,   -- Invariant 4
            target     TEXT NOT NULL,
            request    JSONB NOT NULL,
            state      TEXT NOT NULL DEFAULT 'pending',
            attempts   INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    # ═══════════ MEASUREMENT ═══════════
    # tenant_id stays nullable: a NULL row is platform-scoped (ADR-012).
    op.execute(
        """
        CREATE TABLE experiment_config (
            experiment_id TEXT PRIMARY KEY,
            tenant_id     TEXT REFERENCES tenants(tenant_id),   -- ADR-008; nullable by design
            seed          TEXT NOT NULL,
            control_pct   NUMERIC(4,3) NOT NULL,
            git_commit    TEXT NOT NULL,
            committed_at  TIMESTAMPTZ NOT NULL,
            frozen        BOOLEAN NOT NULL DEFAULT false
        );
        """
    )

    op.execute(
        """
        CREATE TABLE issuer_health (
            issuer_code  TEXT NOT NULL,
            rail         TEXT NOT NULL,
            bucket_start TIMESTAMPTZ NOT NULL,
            n_total      INT NOT NULL,
            n_success    INT NOT NULL,
            wilson_lower NUMERIC(5,4) NOT NULL,
            cusum_stat   NUMERIC(8,4) NOT NULL,
            status       TEXT NOT NULL,
            recovery_eta TIMESTAMPTZ,
            PRIMARY KEY (issuer_code, rail, bucket_start)
        );
        """
    )


def downgrade() -> None:
    # Reverse dependency order. Dropping the partitioned parent drops its
    # partitions, so they need no separate handling.
    for table in (
        "issuer_health",
        "experiment_config",
        "outbox",
        "scheduled_actions",
        "decisions",
        "compliance_rules",
        "segment_priors",
        "customer_profiles",
        "interventions",
        "attempts",
        "cycles",
        "mandates",
        "events_raw",
        "tenants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
