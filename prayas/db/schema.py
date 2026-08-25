"""Canonical registry of which tables are tenant-scoped.

Governing spec: Master Spec §18 and §36.

This exists so the isolation suite has something to assert *against*. The
metatest in ``tests/isolation`` reads the live database and fails if any table
carrying a ``tenant_id`` column is absent from ``TENANT_SCOPED_TABLES`` — which
is what makes the rule in `the tenancy rules` ("any new tenant-scoped
table must be added to the isolation test suite in the same commit that creates
it") enforceable rather than aspirational.

Deliberately **not** duplicated into the migrations: a migration is a historical
record frozen at its revision, and must not drift when this file changes.
"""

from __future__ import annotations

from typing import Final

#: Tables carrying a tenant discriminator, each of which must have RLS enabled,
#: forced, and a policy keyed on ``tenant_id``.
TENANT_SCOPED_TABLES: Final[frozenset[str]] = frozenset(
    {
        # ADR-013. `tenant_id` here is the primary key, and `config` holds policy
        # weights, fatigue caps and kill switches (§18) — competitor-visible data
        # if left global. The metatest below is what caught this.
        "tenants",
        "events_raw",
        "mandates",
        "cycles",
        "attempts",
        "interventions",
        "customer_profiles",
        "decisions",
        "scheduled_actions",
        "outbox",
        "experiment_config",
        # ADR-014. Holds secret *references*, never secret material.
        "webhook_secrets",
    }
)

#: Cross-tenant by design. Each entry is a decision, not an omission.
GLOBAL_TABLES: Final[frozenset[str]] = frozenset(
    {
        "segment_priors",  # k-anonymised aggregates, no PII (§27, Invariant 8)
        "compliance_rules",  # the rule pack is global (§30.1)
        "issuer_health",  # keyed by issuer and rail, not by tenant (§21)
    }
)

#: ``experiment_config.tenant_id`` is nullable: a NULL row is platform-scoped and
#: visible to every tenant, because §33 randomises at customer level and one
#: customer may hold mandates with several merchants (ADR-012).
NULLABLE_TENANT_TABLES: Final[frozenset[str]] = frozenset({"experiment_config"})

#: Invariant 5 — append-only. The app role holds INSERT and SELECT, nothing more.
APPEND_ONLY_TABLES: Final[frozenset[str]] = frozenset({"decisions"})

#: The role the application connects as (ADR-004). Owns nothing.
APP_ROLE: Final = "prayas_app"
