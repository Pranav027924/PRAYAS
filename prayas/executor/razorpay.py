"""Razorpay adapter for `RailProvider` (ADR-092; Master Spec §31, §33).

Everything §31 demands of a rail lives here and nowhere else: the executor
still codes against the protocol and learns none of this.

Three points where a plausible-looking implementation would be wrong:

**A timeout is not a failure.** `httpx` raising `ReadTimeout` on a debit means
the debit may have succeeded. §31 forbids re-issuing it as a fresh charge, so
transport failure maps to `AMBIGUOUS` and the attempt holds its budget slot
until `fetch_by_key` resolves it. Only errors that Razorpay itself reports —
where we hold a response saying nothing was created — are `RETRIABLE`.

**Idempotency is ours to enforce, not Razorpay's.** Razorpay's recurring-charge
endpoint takes no idempotency header. What it does take is `receipt`, which is
unique per order, and that is the hook we hang §31's guarantee on: the
idempotency key goes in `receipt`, and a duplicate submission collides server
side rather than charging twice. `fetch_by_key` then finds the attempt by that
same receipt. Sending the key in `notes` instead would look similar and be
unenforced — notes are free-form metadata Razorpay does not constrain.

**HTTP 200 does not mean the money moved.** Razorpay acknowledges the charge
request; the authoritative result arrives by webhook (§33). `ACCEPTED` here
means "accepted for processing", which is what the executor's state machine
already expects.

Inbound webhook signatures are verified by `prayas.ingest.verify`, which
owns that concern and supports rotation windows. This module is outbound only.

Not yet exercised against a live mandate — see FINDING-P17-03. The account
this was developed against has the Subscriptions product disabled, and in
Razorpay a mandate is a subscription, so no debit has been fired through this.

What *was* verified against the live API is authentication only. The outcome
mapping and the query path are covered by tests against `httpx.MockTransport`,
which pins the logic but not the assumption that Razorpay's real responses
have the shape assumed here. Treat the field names as unconfirmed until a
recorded fixture replaces them (ADR-090).
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from prayas.config import RazorpayCredentials
from prayas.executor.provider import Outcome, ProviderResponse

log = logging.getLogger(__name__)

#: Razorpay error codes that mean "we definitively did not create a charge".
#: Anything outside this set is treated as ambiguous, which is the safe
#: direction to be wrong in: a held budget slot costs a retry, a wrongly
#: cleared one costs a double debit.
_DEFINITELY_NOTHING_HAPPENED: Final[frozenset[str]] = frozenset(
    {"BAD_REQUEST_ERROR", "GATEWAY_ERROR"}
)

#: Razorpay's own timeout/queue signals, where a retry is safe.
_RETRIABLE_STATUS: Final[frozenset[int]] = frozenset({429, 502, 503, 504})

#: `error.reason` values that mean *we* sent a bad request, not that the
#: customer's bank refused one. Confirmed against the live test API: a bogus
#: token returns `BAD_REQUEST_ERROR` with `source: "internal"`,
#: `step: "payment_initiation"`, `reason: "input_validation_failed"` — the same
#: `code` a genuine decline carries.
_OUR_BUG_REASONS: Final[frozenset[str]] = frozenset({"input_validation_failed"})

#: `error.source` values meaning the request never left Razorpay's front door.
_OUR_BUG_SOURCES: Final[frozenset[str]] = frozenset({"internal"})


class RazorpayConfigError(RuntimeError):
    """Credentials absent or malformed. Raised at construction, not at fire time."""


@dataclass
class RazorpayProvider:
    """`RailProvider` over Razorpay's REST API.

    Constructed once and shared: `httpx.AsyncClient` pools connections, and
    building one per debit would add a TLS handshake to every charge.
    """

    credentials: RazorpayCredentials
    timeout_seconds: float = 30.0
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.credentials.configured:
            raise RazorpayConfigError(
                "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must both be set. "
                "A deployment in OBSERVE or SHADOW fires nothing and should not "
                "construct this provider at all."
            )

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            key_id = self.credentials.key_id or ""
            secret = self.credentials.key_secret or ""
            token = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
            self._client = httpx.AsyncClient(
                base_url=self.credentials.base_url,
                headers={
                    "Authorization": f"Basic {token}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def submit_debit(
        self, *, idem_key: str, amount_paise: int, mandate_id: str, request: dict[str, Any]
    ) -> ProviderResponse:
        """Charge `mandate_id` for `amount_paise` under `idem_key`.

        The key travels as the order `receipt`, which Razorpay enforces as
        unique — that is what makes a repeat submission one debit rather than
        two.
        """
        if amount_paise <= 0:
            raise ValueError(f"amount must be positive paise, got {amount_paise}")

        payload = {
            "amount": amount_paise,  # Razorpay speaks paise natively.
            "currency": "INR",
            "receipt": idem_key,
            "customer_id": request.get("customer_id"),
            "token": mandate_id,
            "recurring": "1",
            "description": request.get("description", "PRAYAS recovery attempt"),
        }

        try:
            response = await self.client.post("/payments/create/recurring", json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # The request may have been received and acted on. §31: never
            # re-issue as a new charge; hold the slot and reconcile by key.
            log.warning("razorpay.ambiguous", extra={"idem_key": idem_key, "error": str(exc)})
            return ProviderResponse(
                Outcome.AMBIGUOUS, detail=f"transport failure: {type(exc).__name__}"
            )

        return self._interpret(response, idem_key)

    def _interpret(self, response: httpx.Response, idem_key: str) -> ProviderResponse:
        """Map an HTTP response onto §31's outcome taxonomy."""
        if response.status_code in _RETRIABLE_STATUS:
            return ProviderResponse(Outcome.RETRIABLE, detail=f"razorpay {response.status_code}")

        try:
            body = response.json()
        except ValueError:
            # A 2xx we cannot parse is the worst case: it may describe a
            # successful charge. Ambiguous, not retriable.
            return ProviderResponse(
                Outcome.AMBIGUOUS, detail=f"unparseable body, http {response.status_code}"
            )

        if response.is_success:
            return ProviderResponse(
                Outcome.ACCEPTED,
                provider_ref=body.get("id"),
                detail=f"status={body.get('status', 'unknown')}",
            )

        error = body.get("error", {}) if isinstance(body, dict) else {}
        code = str(error.get("code", ""))
        description = str(error.get("description", ""))[:200]

        reason = str(error.get("reason", ""))
        source = str(error.get("source", ""))
        step = str(error.get("step", ""))

        # A malformed request is OUR defect and must never be recorded as a
        # decline. Razorpay reuses `BAD_REQUEST_ERROR` for both, so the code
        # alone cannot tell them apart — `reason`/`source` can.
        #
        # Calling this DECLINED would charge a customer's failure statistics
        # for our bug: §21's hazard model learns from decline codes, and the
        # cycle would carry a refusal its bank never issued. RETRIABLE is the
        # honest reading — definitively nothing happened — and it keeps the
        # budget slot unconsumed (it is absent from HOLDS_BUDGET). The retry
        # will fail identically, which is the correct loud failure for a bug.
        if reason in _OUR_BUG_REASONS or source in _OUR_BUG_SOURCES:
            log.error(
                "razorpay.malformed_request",
                extra={
                    "idem_key": idem_key,
                    "reason": reason,
                    "source": source,
                    "step": step,
                    "description": description,
                },
            )
            return ProviderResponse(
                Outcome.RETRIABLE, detail=f"our bad request ({reason}): {description}"
            )

        # A declined *payment* is a business outcome and must not be retried
        # as a transport error; the sequencer decides what happens next.
        if code in _DEFINITELY_NOTHING_HAPPENED:
            return ProviderResponse(
                Outcome.DECLINED,
                decline_code=reason or code,
                detail=description,
            )

        # Unrecognised error shape. Ambiguous is the conservative reading.
        log.warning(
            "razorpay.unmapped_error",
            extra={"idem_key": idem_key, "code": code, "http": response.status_code},
        )
        return ProviderResponse(Outcome.AMBIGUOUS, detail=f"unmapped error {code}: {description}")

    async def fetch_by_key(self, idem_key: str) -> ProviderResponse | None:
        """Find what happened under `idem_key`, submitting nothing.

        Queries by the `receipt` the debit was filed under. `None` means
        Razorpay holds no record, so the debit definitively did not happen.
        """
        try:
            response = await self.client.get("/orders", params={"receipt": idem_key})
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            # A failed *query* changed nothing. Report ambiguity so the caller
            # leaves the slot held and tries the query again later.
            return ProviderResponse(Outcome.AMBIGUOUS, detail=f"query failed: {type(exc).__name__}")

        if not response.is_success:
            return ProviderResponse(Outcome.AMBIGUOUS, detail=f"query http {response.status_code}")

        try:
            body = response.json()
        except ValueError:
            return ProviderResponse(Outcome.AMBIGUOUS, detail="unparseable query body")

        items = body.get("items", []) if isinstance(body, dict) else []
        if not items:
            return None  # No record: the debit did not happen.

        order = items[0]
        status = str(order.get("status", ""))
        # Razorpay order status: created | attempted | paid
        if status == "paid":
            return ProviderResponse(
                Outcome.ACCEPTED, provider_ref=str(order.get("id")), detail="reconciled paid"
            )
        if status == "created":
            # The order exists but nothing was charged against it.
            return ProviderResponse(
                Outcome.DECLINED,
                decline_code="reconciled_not_charged",
                detail="order created, never attempted",
            )
        # "attempted" genuinely means in flight — still ambiguous.
        return ProviderResponse(Outcome.AMBIGUOUS, detail=f"order status={status}")
