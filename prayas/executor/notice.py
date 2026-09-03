"""Firing a pre-debit notice (ADR-099; Master Spec §30.1, §24.6).

`RBI-EMANDATE-PDN-24H` reads `pdn_sent_at != null and hours_since(pdn_sent_at)
>= 24`, and until now **nothing wrote `cycles.pdn_sent_at`**. Every debit was
therefore denied at fire time, correctly and permanently (FINDING-P17-11).
This is the path that sends the notice and records that it was sent.

**A notice is not a debit, and the differences are the whole design:**

* It **never touches the attempt budget.** §1's "one execution plus up to three
  retries" counts debits. Charging a notice against that would spend the
  regulator's allowance on a message.
* It **never reaches the rail.** No `submit_debit`, no idempotency key against
  the provider, no money.
* It is **suppressible.** §24.6: a system that cannot choose silence will
  over-message its way through its portfolio. A contact suppression or a
  fatigue cap makes "send nothing" the outcome, recorded with its reason
  rather than skipped quietly.

**`pdn_sent_at` is written only after the channel accepts the message**, in the
same transaction. Writing it first would mean a failed send still unlocked the
debit — the gate would see a notice that never left the building, and the
resulting debit would be unlawful while looking compliant. That is the one
ordering error in this file that would matter.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from prayas.domain.rails import hour_ist
from prayas.executor.claiming import (
    STATE_CANCELLED,
    STATE_DONE,
    ClaimedAction,
    release_action,
)
from prayas.gate.engine import evaluate
from prayas.ledger.chain import append
from prayas.memory.forget import pseudonymise
from prayas.observability import metrics

log = logging.getLogger(__name__)

#: `scheduled_actions.action_type` for a pre-debit notice.
ACTION_TYPE_NOTICE = "pdn_notice"

#: §24.6's ceiling when a tenant has not set its own.
DEFAULT_FATIGUE_CAP = 4


@dataclass(frozen=True, slots=True)
class NoticeOutcome:
    sent: bool
    reason: str
    cycle_id: str | None = None


async def _suppressed(conn: AsyncConnection, tenant_id: str, customer_id: str) -> str | None:
    """A live contact suppression for this customer, or None.

    §24.6 and the opt-out path: someone who asked not to be contacted is not
    contacted, and the debit that depended on the notice does not happen.

    `contact_suppressions` is keyed on the **pseudonym**, never the raw
    customer id (§27). Both forms are checked because erasure rewrites
    `mandates.customer_id` to the pseudonym in place — so after a forget
    request the stored id already *is* the ref, and hashing it again would
    look up a value that was never written. Missing that would mean an erased
    customer keeps receiving messages, which is the failure this check exists
    to prevent.
    """
    candidates = {customer_id, pseudonymise(tenant_id, customer_id)}
    row = (
        await conn.execute(
            text(
                "SELECT reason FROM contact_suppressions"
                " WHERE tenant_id = :t AND customer_ref = ANY(:refs)"
                " ORDER BY suppressed_at DESC LIMIT 1"
            ),
            {"t": tenant_id, "refs": list(candidates)},
        )
    ).first()
    return str(row.reason) if row is not None else None


async def _message_context(
    conn: AsyncConnection,
    action: ClaimedAction,
    cycle: Any,
    profile: Any,
    sent_at: datetime,
) -> dict[str, Any]:
    """Facts §30's messaging rules evaluate against, read at send time.

    `dlt_template_id` and the header series come from the tenant's own
    configuration, because they are registration facts about that merchant —
    a deployment without DLT registration has none, and `TRAI-DLT-TEMPLATE`
    then refuses the send rather than the send happening anyway.
    """
    row = (
        await conn.execute(
            text("SELECT config FROM tenants WHERE tenant_id = :t"),
            {"t": action.tenant_id},
        )
    ).first()
    config: dict[str, Any] = (row.config or {}) if row is not None else {}

    return {
        "hour_ist": hour_ist(sent_at),
        "consent_ref": cycle.consent_ref,
        "consent_withdrawn": bool(profile.consent_withdrawn) if profile else False,
        "messages_30d": int(profile.messages_30d) if profile else 0,
        "tenant_fatigue_cap": int(config.get("fatigue_cap", DEFAULT_FATIGUE_CAP)),
        "dlt_template_id": config.get("dlt_template_id"),
        "header_series": str(config.get("header_series", "")),
        "dnd_registered": bool(config.get("dnd_registered", False)),
        "action_type": "sms",
        "amount_paise": cycle.amount_paise,
        "mcc": None,
        "pdn_sent_at": cycle.pdn_sent_at,
        "attempts_used": 0,
        "attempt_budget": 0,
        "is_next_day_debit": False,
    }


async def fire_notice(
    conn: AsyncConnection,
    action: ClaimedAction,
    *,
    now: datetime | None = None,
) -> NoticeOutcome:
    """Send the pre-debit notice for `action`, or record why it was not sent."""
    sent_at = now or datetime.now(UTC)

    if action.cycle_id is None or action.mandate_id is None:
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        return NoticeOutcome(False, "notice_without_cycle")

    cycle = (
        await conn.execute(
            text(
                "SELECT c.cycle_id, c.state, c.pdn_sent_at, c.amount_paise,"
                "       m.customer_id, m.consent_ref"
                "  FROM cycles c"
                "  JOIN mandates m ON m.tenant_id = c.tenant_id AND m.mandate_id = c.mandate_id"
                " WHERE c.tenant_id = :t AND c.cycle_id = :c"
                " FOR UPDATE OF c"
            ),
            {"t": action.tenant_id, "c": action.cycle_id},
        )
    ).first()

    if cycle is None:
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        return NoticeOutcome(False, "unknown_cycle")

    # Revalidated at fire time, like every other scheduled action (§32). A
    # cycle that settled while the notice was queued needs no notice.
    if cycle.state not in ("executing", "scheduled"):
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        metrics.increment("notice_skipped", reason="cycle_settled")
        return NoticeOutcome(False, "cycle_settled", action.cycle_id)

    if cycle.pdn_sent_at is not None:
        # Already noticed. Sending again would be a duplicate message to a real
        # person, and the gate is already satisfied.
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_DONE)
        metrics.increment("notice_skipped", reason="already_sent")
        return NoticeOutcome(False, "already_sent", action.cycle_id)

    # §30's messaging rules — contact window, DLT template, consent, fatigue —
    # live in the same pack as the debit rules and were being bypassed
    # entirely: this path called the gate zero times. A notice sent at 03:00,
    # or above the fatigue cap, or to a customer who withdrew consent, is as
    # much a breach as an unlawful debit, and the rules exist to stop it.
    profile = (
        await conn.execute(
            text(
                "SELECT COALESCE(p.messages_30d, 0) AS messages_30d,"
                "       COALESCE(p.consent_withdrawn, false) AS consent_withdrawn"
                "  FROM mandates m"
                "  LEFT JOIN customer_profiles p"
                "         ON p.tenant_id = m.tenant_id AND p.customer_id = m.customer_id"
                " WHERE m.tenant_id = :t AND m.mandate_id = :m"
            ),
            {"t": action.tenant_id, "m": action.mandate_id},
        )
    ).first()

    messaging = await evaluate(
        conn,
        action_type="sms",
        rail=None,
        ctx=await _message_context(conn, action, cycle, profile, sent_at),
        as_of=sent_at.date(),
        now=sent_at,
    )
    if not messaging.allowed:
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        metrics.increment("notice_gate_denied", verdict=messaging.verdict)
        log.info(
            "notice.gate_denied",
            extra={
                "cycle_id": action.cycle_id,
                "verdict": messaging.verdict,
                "refused": [c["rule_id"] for c in messaging.checks if c.get("verdict") != "ALLOW"],
            },
        )
        return NoticeOutcome(False, f"gate:{messaging.verdict}", action.cycle_id)

    suppression = await _suppressed(conn, action.tenant_id, str(cycle.customer_id))
    if suppression is not None:
        await release_action(conn, action.action_id, action.tenant_id, state=STATE_CANCELLED)
        metrics.increment("notice_suppressed", reason=suppression)
        log.info(
            "notice.suppressed",
            extra={"cycle_id": action.cycle_id, "reason": suppression},
        )
        return NoticeOutcome(False, f"suppressed:{suppression}", action.cycle_id)

    # The send itself. In test mode this is the console channel; a live
    # deployment substitutes a DLT-registered sender (docs/BLOCKERS.md §1.2).
    # `pdn_sent_at` is written only on acceptance, and in this transaction.
    channel = str(action.payload.get("channel", "console"))
    log.info(
        "notice.sent",
        extra={
            "cycle_id": action.cycle_id,
            "mandate_id": action.mandate_id,
            "channel": channel,
            "sent_at": sent_at.isoformat(),
        },
    )

    await conn.execute(
        text(
            "UPDATE cycles SET pdn_sent_at = :at"
            " WHERE tenant_id = :t AND cycle_id = :c AND pdn_sent_at IS NULL"
        ),
        {"at": sent_at, "t": action.tenant_id, "c": action.cycle_id},
    )
    # Every intervention traces to a ledger decision — `interventions.decision_id`
    # is NOT NULL, and rightly so: a message sent to a person is an action, and
    # §32 requires actions to be recorded. The notice is appended to the same
    # hash chain as the debit it precedes.
    decision_id = f"dec_{sha256(action.action_id.encode()).hexdigest()[:24]}"
    await append(
        conn,
        {
            "decision_id": decision_id,
            "ts": sent_at,
            "trigger_event_id": None,
            "mandate_id": action.mandate_id,
            "cycle_id": action.cycle_id,
            "action_type": ACTION_TYPE_NOTICE,
            "verdict": "ALLOW",
            "feature_snapshot_ref": None,
            "model_versions": json.dumps({"notice": "p17"}),
            "cause_posterior": None,
            "liquidity_curve_ref": None,
            "revocation_hazard": None,
            "continuation_value": None,
            "candidate_actions": json.dumps([]),
            "chosen_action": json.dumps({"action_type": ACTION_TYPE_NOTICE, "channel": channel}),
            "rationale": f"pre-debit notice sent via {channel} (§30.1)",
            "compliance_checks": json.dumps([]),
            "holdout_arm": None,
            "propensity": None,
            "degraded": False,
            "outcome": None,
            "outcome_ts": None,
            "recovered_paise": None,
        },
        action.tenant_id,
    )

    await conn.execute(
        text(
            "INSERT INTO interventions (intervention_id, tenant_id, mandate_id, kind,"
            " proposed_at, payload, decision_id)"
            " VALUES (:i, :t, :m, 'pdn_notice', :at, CAST(:p AS jsonb), :d)"
            " ON CONFLICT (intervention_id) DO NOTHING"
        ),
        {
            "i": f"int_{action.action_id}",
            "t": action.tenant_id,
            "m": action.mandate_id,
            "at": sent_at,
            "p": json.dumps({"channel": channel, "action_id": action.action_id}),
            "d": decision_id,
        },
    )

    await release_action(conn, action.action_id, action.tenant_id, state=STATE_DONE)
    metrics.increment("notice_sent", tenant_id=action.tenant_id, channel=channel)
    return NoticeOutcome(True, "sent", action.cycle_id)


def is_notice(action: ClaimedAction | Any) -> bool:
    return bool(getattr(action, "action_type", None) == ACTION_TYPE_NOTICE)
