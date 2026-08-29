"""Turning a simulated population into §20's cause-inference observations.

Every *context* feature here is built from the observable stream and the issuer
nowcast. Ground truth is read in exactly two places, and neither is a context
feature:

1. `true_cause`, returned separately, purely to score. Nothing sees it.
2. `true_funding_time`, to derive §20's training-time outcome — "the eventual
   outcome is a noisy label for the latent cause". In production this is
   observed directly: the retry that cleared, and how long it took. Here the
   simulator supplies the same fact without having to execute the retry.

The distinction matters because the outcome is legitimately available when
*training* and never available when *deciding* — which is why `CauseEM` keeps
it in a separate likelihood factor and drops that factor at serve time. Reading
it here is modelling what a production system sees, not peeking at the answer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Final

import numpy as np

from prayas.inference.cause import CauseContext
from prayas.inference.em import Observation
from prayas.inference.nowcast import IssuerNowcast, peer_context
from prayas.sim.generate import SimulatedCycle

#: Codes whose cause the issuer stated. These anchor the EM (§20).
ANCHOR_CODES: Final[frozenset[str]] = frozenset({"51", "91", "61"})


def _entity(event: dict[str, Any]) -> dict[str, Any]:
    entity = event.get("body", {}).get("payload", {}).get("payment", {}).get("entity", {})
    return entity if isinstance(entity, dict) else {}


def failure_of(cycle: SimulatedCycle) -> tuple[datetime, str] | None:
    """`(when, decline_code)` of the cycle's failure, or None if it settled."""
    for event in cycle.observables:
        body = event.get("body", {})
        if body.get("event") != "payment.failed":
            continue
        entity = _entity(event)
        code = entity.get("error_code") or entity.get("decline_code") or ""
        return datetime.fromtimestamp(int(body["created_at"]), tz=UTC), str(code)
    return None


def succeeded(cycle: SimulatedCycle) -> bool:
    return any(e.get("body", {}).get("event") == "payment.captured" for e in cycle.observables)


def build_nowcast(cycles: list[SimulatedCycle], **kwargs: Any) -> IssuerNowcast:
    """Replay every attempt through the nowcast, in chronological order.

    Order matters: the nowcast is a streaming detector, and feeding it a
    population out of order would let a later window close an earlier one.

    **The healthy baseline is measured, not assumed.** It is the success rate
    this traffic actually achieves when nothing is wrong, and it differs by an
    order of magnitude between a card portfolio and a recurring-debit book
    where most declines are empty accounts. Hard-coding a card-like 82% against
    a population that clears 44% makes every issuer look permanently degraded,
    and the cause heuristic then attributes every decline to an outage.
    """
    attempts: list[tuple[datetime, str, bool]] = []
    for cycle in cycles:
        failure = failure_of(cycle)
        if failure is not None:
            attempts.append((failure[0], cycle.issuer, False))
        elif succeeded(cycle):
            attempts.append((cycle.due_at, cycle.issuer, True))

    if "baseline_success" not in kwargs and attempts:
        observed = sum(1 for a in attempts if a[2]) / len(attempts)
        # Floored: a book that clears almost nothing still has to leave room
        # for an outage to look worse than its ordinary bad day.
        kwargs["baseline_success"] = max(observed, 0.05)

    nowcast = IssuerNowcast(**kwargs)
    for at, issuer, ok in sorted(attempts, key=lambda a: a[0]):
        nowcast.observe(issuer, at, success=ok)
    if attempts:
        nowcast.flush(max(a[0] for a in attempts) + timedelta(days=1))
    return nowcast


def build_observations(
    cycles: list[SimulatedCycle], nowcast: IssuerNowcast
) -> tuple[list[Observation], list[str]]:
    """§20's observations and their ground-truth causes, aligned.

    The truth list is returned *separately* from the observations so that
    passing one to the model without the other is the natural thing to do, and
    passing both would take deliberate effort. ADR-030's discipline as a shape,
    not as a comment.
    """
    by_customer: dict[str, list[SimulatedCycle]] = {}
    for cycle in cycles:
        by_customer.setdefault(cycle.customer_id, []).append(cycle)
    for owned in by_customer.values():
        owned.sort(key=lambda c: c.due_at)

    observations: list[Observation] = []
    truth: list[str] = []

    for cycle in cycles:
        failure = failure_of(cycle)
        if failure is None:
            continue
        at, code = failure

        siblings = by_customer[cycle.customer_id]
        index = siblings.index(cycle)
        prior = siblings[:index]

        # §20 "this customer's other mandates, same window".
        concurrent = [
            other
            for other in siblings
            if other is not cycle and abs((other.due_at - at).total_seconds()) <= 6 * 3600
        ]
        sibling_failures = sum(1 for other in concurrent if failure_of(other) is not None)
        sibling_rate = sibling_failures / len(concurrent) if concurrent else 0.0

        # §20 "amount / p75(customer successful debits)".
        settled = [c.amount_paise for c in prior if succeeded(c)]
        p75 = float(np.percentile(settled, 75)) if settled else float(cycle.amount_paise)
        amount_ratio = cycle.amount_paise / p75 if p75 > 0 else 1.0

        # §20 "days_since_inferred_payday", from the customer's own history.
        success_days = [c.due_at.day for c in prior if succeeded(c)]
        payday = int(np.bincount(success_days, minlength=32).argmax()) if success_days else None
        days_since = (at.day - payday) % 30 if payday is not None else None

        wilson, peer = peer_context(nowcast, cycle.issuer, at)

        # The training-time outcome (§20). A cycle whose funds arrived inside
        # the horizon would have been recovered by a retry; that retry's outcome
        # is what a production system observes and trains on.
        funded_at = cycle.truth.true_funding_time
        recovered = funded_at is not None and funded_at > at
        lag = (funded_at - at).total_seconds() / 3600.0 if recovered and funded_at else None

        observations.append(
            Observation(
                decline_code=code,
                context=CauseContext(
                    issuer_wilson_lower=wilson,
                    peer_success_rate=peer,
                    sibling_failure_rate=sibling_rate,
                    amount_ratio=amount_ratio,
                    days_since_inferred_payday=days_since,
                    n_concurrent_mandates=max(len(concurrent), 1),
                ),
                recovered=bool(recovered),
                recovery_lag_hours=lag,
            )
        )
        truth.append(cycle.truth.true_cause)

    return observations, truth
