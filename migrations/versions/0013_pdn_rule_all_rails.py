"""RBI-EMANDATE-PDN-24H v3 — the notice rule covers every rail (FINDING-P14-01).

Version 2 carried `rails: [upi_autopay, card_emandate, enach]`. Those were all
the rails that existed, so it behaved identically to `rails: null` — but the
gate filters `rails IS NULL OR :rail = ANY(rails)`, so a rail added later would
have inherited the universal rules and **silently escaped the 24-hour notice
requirement**. The failure would have been quiet, because the resulting debit
looks lawful.

Version 3 sets `rails: null`.

**A new version, not an edit** (ADR-020, and the rulepack's own header): "the
ledger records which version governed each past decision, and rewriting a
version retroactively falsifies that record." Version 2 stays exactly as it
was.

**`as_of` is today, not the regulation's date.** The RBI framework has not
changed and its citation is untouched; what changed is our encoding of which
rails it covers. Dating version 3 to 2026-08-30 preserves §30.1's replay
property — `load_active_rules` orders by `as_of DESC`, so a decision replayed
from before today still selects version 2, which is what actually governed it.
Back-dating would have rewritten history in the one place §30.1 says must not
be rewritten.

Revision ID: 0013_pdn_rule_all_rails
Revises: 0012_batch_presentation
Create Date: 2026-08-30

"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0013_pdn_rule_all_rails"
down_revision: str | None = "0012_batch_presentation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RULE_ID = "RBI-EMANDATE-PDN-24H"
VERSION = 3

# Transcribed inline rather than re-read from the YAML: a migration is a
# historical record, and one that re-parsed a file which keeps changing would
# load something different on every fresh database.
PREDICATE = "pdn_sent_at != null and hours_since(pdn_sent_at) >= 24"

#: Unchanged from version 2 — the regulation did not change, our encoding of
#: which rails it covers did. Invariant 10 wants the citation intact.
CITATION = "Digital Payments \u2013 E-mandate Framework, 2026"

#: The date this *encoding* takes effect. See the module docstring.
AS_OF = date(2026, 8, 30)


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO compliance_rules"
            " (rule_id, version, regulator, citation, as_of, applies_to, rails,"
            "  predicate, on_fail, active, created_by)"
            " VALUES (:rule_id, :version, 'RBI', :citation, :as_of,"
            "         ARRAY['debit_attempt'], NULL, :predicate, 'DENY', true,"
            "         'finding-p14-01')"
            " ON CONFLICT (rule_id, version) DO NOTHING"
        ),
        {
            "rule_id": RULE_ID,
            "version": VERSION,
            "as_of": AS_OF,
            "citation": CITATION,
            "predicate": PREDICATE,
        },
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM compliance_rules WHERE rule_id = :rule_id AND version = :version"),
        {"rule_id": RULE_ID, "version": VERSION},
    )
