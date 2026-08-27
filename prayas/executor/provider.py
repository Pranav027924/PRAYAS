"""Rail provider seam and its Phase 6 stand-in (ADR-043; Master Spec §31).

The executor codes against `RailProvider` and never learns a provider's
specifics. Phase 17 drops a real Razorpay adapter in behind this protocol; no
code in this phase calls an external service.

The outcome taxonomy is §31's, and the distinction that matters is
`AMBIGUOUS`:

    "A timeout on a debit means the debit *may* have happened. Never re-issue
    as a new charge; retry with the same key, then reconcile by key. The
    attempt holds its budget slot until resolved - pessimistic and correct."

So `AMBIGUOUS` is not an error to be retried into a fresh charge. It is a state
the attempt sits in until reconciliation resolves it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Protocol, runtime_checkable


class Outcome(StrEnum):
    """What a provider call tells us about the money."""

    #: The debit was accepted. The definitive result still arrives by webhook.
    ACCEPTED = "accepted"
    #: The provider declined. No money moved.
    DECLINED = "declined"
    #: Transport or provider failure with a definite "nothing happened".
    RETRIABLE = "retriable"
    #: Timeout or unparseable response. The debit MAY have happened.
    AMBIGUOUS = "ambiguous"


#: Outcomes after which the budget slot must be held pessimistically (§31).
HOLDS_BUDGET: Final[frozenset[Outcome]] = frozenset({Outcome.ACCEPTED, Outcome.AMBIGUOUS})


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    outcome: Outcome
    provider_ref: str | None = None
    decline_code: str | None = None
    detail: str = ""


@runtime_checkable
class RailProvider(Protocol):
    """What the executor needs from a rail, and nothing more."""

    async def submit_debit(
        self, *, idem_key: str, amount_paise: int, mandate_id: str, request: dict[str, Any]
    ) -> ProviderResponse:
        """Submit a debit under `idem_key`.

        Must be idempotent on the key: submitting the same key twice is one
        debit. That is the provider's half of §31's defence in depth.
        """
        ...

    async def fetch_by_key(self, idem_key: str) -> ProviderResponse | None:
        """Ask what happened under this key, without submitting anything.

        This is what reconciliation uses. §31 says to "reconcile by key", and
        a *query* is the only safe way to do that: re-submitting to discover
        the outcome would be indistinguishable from trying again.

        Returns None when the provider has no record of the key — meaning the
        debit definitively did not happen.
        """
        ...


@dataclass
class FakeProvider:
    """Deterministic fault-injecting stand-in (ADR-043).

    Behaviour is a pure function of the idempotency key and the configured
    rates, so a run is reproducible and "10% injected timeouts" means exactly
    that rather than roughly that.

    Idempotent by key, like the real thing: a repeat submission returns the
    first response and does not count as a second debit. `debits_by_key`
    therefore *is* the ground truth a double-debit assertion checks.
    """

    timeout_rate: float = 0.0
    decline_rate: float = 0.0
    retriable_rate: float = 0.0
    seed: str = "fake"

    def __post_init__(self) -> None:
        total = self.timeout_rate + self.decline_rate + self.retriable_rate
        if total > 1.0:
            raise ValueError(f"injected failure rates sum to {total}, above 1.0")
        for name in ("timeout_rate", "decline_rate", "retriable_rate"):
            rate = getattr(self, name)
            if not 0.0 <= rate <= 1.0:
                raise ValueError(f"{name} must be a probability, got {rate}")
        #: key -> the single response that key ever produced.
        self.debits_by_key: dict[str, ProviderResponse] = {}
        #: Every submission, including repeats. Length > len(debits_by_key)
        #: means retries happened, which is expected and safe.
        self.submissions: list[str] = []
        #: What *actually* happened behind a timeout, hidden from the caller
        #: until it reconciles. A timeout that really charged is the case the
        #: whole ambiguous path exists for.
        self._truth_behind_timeout: dict[str, bool] = {}
        #: Keys queried via `fetch_by_key`. Reconciliation must query, never
        #: re-submit, and this is what proves it did.
        self.fetches: list[str] = []

    def _roll(self, idem_key: str) -> float:
        """Uniform in [0, 1), deterministic in the key and the seed."""
        digest = hashlib.sha256(f"{self.seed}:{idem_key}".encode()).digest()
        return int.from_bytes(digest[:8], "big") / float(1 << 64)

    async def submit_debit(
        self, *, idem_key: str, amount_paise: int, mandate_id: str, request: dict[str, Any]
    ) -> ProviderResponse:
        self.submissions.append(idem_key)

        # Idempotent by key: the provider's own guarantee, modelled honestly.
        if idem_key in self.debits_by_key:
            return self.debits_by_key[idem_key]

        roll = self._roll(idem_key)
        if roll < self.timeout_rate:
            # Deliberately NOT recorded in debits_by_key: an ambiguous outcome
            # means we do not know whether money moved. Recording it either way
            # would be assuming the answer the reconciler exists to find.
            # Roughly half of real timeouts did charge; that truth is kept
            # hidden here until `fetch_by_key` reveals it.
            self._truth_behind_timeout[idem_key] = self._roll(f"truth:{idem_key}") < 0.5
            return ProviderResponse(Outcome.AMBIGUOUS, detail="provider timeout")

        if roll < self.timeout_rate + self.retriable_rate:
            return ProviderResponse(Outcome.RETRIABLE, detail="provider 503")

        if roll < self.timeout_rate + self.retriable_rate + self.decline_rate:
            response = ProviderResponse(Outcome.DECLINED, decline_code="05")
        else:
            response = ProviderResponse(Outcome.ACCEPTED, provider_ref=f"pay_{idem_key[-12:]}")

        self.debits_by_key[idem_key] = response
        return response

    async def fetch_by_key(self, idem_key: str) -> ProviderResponse | None:
        """Query-only. Reveals what a timeout actually did, charging nothing."""
        self.fetches.append(idem_key)

        if idem_key in self.debits_by_key:
            return self.debits_by_key[idem_key]

        if idem_key in self._truth_behind_timeout:
            charged = self._truth_behind_timeout.pop(idem_key)
            response = (
                ProviderResponse(Outcome.ACCEPTED, provider_ref=f"pay_{idem_key[-12:]}")
                if charged
                else ProviderResponse(Outcome.DECLINED, decline_code="reconciled_not_charged")
            )
            self.debits_by_key[idem_key] = response
            return response

        return None

    @property
    def distinct_debits(self) -> int:
        """Distinct keys that produced a debit. A double debit raises this."""
        return sum(1 for r in self.debits_by_key.values() if r.outcome is Outcome.ACCEPTED)
