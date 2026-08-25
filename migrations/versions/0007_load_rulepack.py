"""Load the compliance rule pack from YAML (ADR-020).

`prayas/gate/rules/rulepack.yaml` is the reviewable source of truth; this
migration is the mechanism that makes "a regulatory change is a data migration"
literally true rather than a slogan.

**Upsert is keyed on `(rule_id, version)`.** Loading is therefore idempotent: an
existing version is left untouched, a new version is inserted. Changing a rule
means *bumping its version*, never editing one in place — the ledger records
which version governed each past decision, and rewriting a version retroactively
falsifies that record.

Parsing is done inline rather than by importing `prayas.gate`, because a
migration is a historical record and must not change behaviour when application
code is refactored.

Revision ID: 0007_load_rulepack
Revises: 0006_regulatory_reference
Create Date: 2026-08-25

"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import sqlalchemy as sa
import yaml
from alembic import op

revision: str = "0007_load_rulepack"
down_revision: str | None = "0006_regulatory_reference"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULEPACK = Path(__file__).resolve().parents[2] / "prayas" / "gate" / "rules" / "rulepack.yaml"


def _load() -> dict[str, Any]:
    with RULEPACK.open(encoding="utf-8") as handle:
        data: dict[str, Any] = yaml.safe_load(handle)
    return data


def upgrade() -> None:
    pack = _load()
    conn = op.get_bind()

    for rule in pack["rules"]:
        conn.execute(
            sa.text(
                "INSERT INTO compliance_rules"
                " (rule_id, version, regulator, citation, as_of, applies_to, rails,"
                "  predicate, on_fail, active, created_by)"
                " VALUES (:rule_id, :version, :regulator, :citation, :as_of, :applies_to,"
                "         :rails, :predicate, :on_fail, true, :created_by)"
                " ON CONFLICT (rule_id, version) DO NOTHING"
            ),
            {
                "rule_id": rule["rule_id"],
                "version": rule["version"],
                "regulator": rule["regulator"],
                "citation": rule["citation"],
                "as_of": rule["as_of"],
                "applies_to": rule["applies_to"],
                "rails": rule.get("rails"),
                "predicate": rule["predicate"],
                "on_fail": rule["on_fail"],
                "created_by": rule.get("created_by", "rulepack"),
            },
        )

    for cap in pack["afa_caps"]:
        conn.execute(
            sa.text(
                "INSERT INTO regulatory_reference"
                " (mcc, cap_paise, regulator, citation, as_of)"
                " VALUES (:mcc, :cap_paise, :regulator, :citation, :as_of)"
                " ON CONFLICT (mcc, as_of) DO NOTHING"
            ),
            cap,
        )


def downgrade() -> None:
    pack = _load()
    conn = op.get_bind()

    for rule in pack["rules"]:
        conn.execute(
            sa.text("DELETE FROM compliance_rules WHERE rule_id = :rule_id AND version = :version"),
            {"rule_id": rule["rule_id"], "version": rule["version"]},
        )

    for cap in pack["afa_caps"]:
        conn.execute(
            sa.text("DELETE FROM regulatory_reference WHERE mcc = :mcc AND as_of = :as_of"),
            {"mcc": cap["mcc"], "as_of": cap["as_of"]},
        )
