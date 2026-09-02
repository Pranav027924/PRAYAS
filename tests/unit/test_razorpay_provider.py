"""Razorpay adapter behaviour (ADR-092; Master Spec §31).

No network. `httpx.MockTransport` supplies the responses, so what is under
test is the mapping from what Razorpay says onto what the executor believes
about the money — which is where a wrong answer costs a double debit.
"""

from __future__ import annotations

import json
from typing import Any, Final

import httpx
import pytest

from prayas.config import RazorpayCredentials
from prayas.executor.provider import HOLDS_BUDGET, Outcome, RailProvider
from prayas.executor.razorpay import RazorpayConfigError, RazorpayProvider

CREDS = RazorpayCredentials(
    key_id="rzp_test_x", key_secret="s3cret", base_url="https://api.test/v1"
)


def _provider(handler: Any, **kwargs: Any) -> RazorpayProvider:
    """A provider whose transport is `handler` rather than the network."""
    provider = RazorpayProvider(credentials=CREDS, **kwargs)
    provider._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=CREDS.base_url
    )
    return provider


def _json(status: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        status, content=json.dumps(body), headers={"content-type": "application/json"}
    )


# ── the protocol seam ────────────────────────────────────────────────────────


def test_satisfies_the_rail_provider_protocol() -> None:
    """The executor codes against `RailProvider`; this must actually be one."""
    assert isinstance(RazorpayProvider(credentials=CREDS), RailProvider)


def test_refuses_to_construct_without_credentials() -> None:
    """Fail at construction, not at fire time.

    A provider that builds fine and then fails mid-debit turns a config
    mistake into an ambiguous attempt.
    """
    blank = RazorpayCredentials(key_id=None, key_secret=None)
    with pytest.raises(RazorpayConfigError):
        RazorpayProvider(credentials=blank)


# ── §31: a timeout is ambiguous, never retriable ─────────────────────────────


@pytest.mark.asyncio
async def test_transport_timeout_is_ambiguous_not_retriable() -> None:
    """The money-safety property. A timeout MAY have charged.

    Calling this RETRIABLE would let the executor re-issue it as a fresh
    charge, which is exactly the double debit §31 forbids.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = await _provider(handler).submit_debit(
        idem_key="k1", amount_paise=50_000, mandate_id="token_x", request={}
    )
    assert result.outcome is Outcome.AMBIGUOUS
    assert result.outcome in HOLDS_BUDGET  # the slot stays held


@pytest.mark.asyncio
async def test_network_error_is_ambiguous() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await _provider(handler).submit_debit(
        idem_key="k2", amount_paise=1, mandate_id="t", request={}
    )
    assert result.outcome is Outcome.AMBIGUOUS


@pytest.mark.asyncio
async def test_unparseable_success_body_is_ambiguous() -> None:
    """A 200 we cannot read may describe a charge that happened."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway</html>")

    result = await _provider(handler).submit_debit(
        idem_key="k3", amount_paise=1, mandate_id="t", request={}
    )
    assert result.outcome is Outcome.AMBIGUOUS


@pytest.mark.asyncio
async def test_unrecognised_error_code_is_ambiguous_not_declined() -> None:
    """Conservative by default.

    Wrongly holding a slot costs a delayed retry. Wrongly releasing one costs
    a second debit, so an unmapped error resolves toward ambiguity.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _json(400, {"error": {"code": "SOME_NEW_CODE", "description": "who knows"}})

    result = await _provider(handler).submit_debit(
        idem_key="k4", amount_paise=1, mandate_id="t", request={}
    )
    assert result.outcome is Outcome.AMBIGUOUS


# ── the ordinary outcomes ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_success_is_accepted_and_carries_the_provider_reference() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(200, {"id": "pay_ABC123", "status": "captured"})

    result = await _provider(handler).submit_debit(
        idem_key="k5", amount_paise=25_000, mandate_id="t", request={}
    )
    assert result.outcome is Outcome.ACCEPTED
    assert result.provider_ref == "pay_ABC123"


@pytest.mark.asyncio
async def test_bad_request_is_declined_with_the_reason_as_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(
            400,
            {
                "error": {
                    "code": "BAD_REQUEST_ERROR",
                    "reason": "insufficient_funds",
                    "description": "Your account has insufficient balance",
                }
            },
        )

    result = await _provider(handler).submit_debit(
        idem_key="k6", amount_paise=1, mandate_id="t", request={}
    )
    assert result.outcome is Outcome.DECLINED
    assert result.decline_code == "insufficient_funds"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_provider_side_overload_is_retriable(status: int) -> None:
    """Razorpay telling us it did not process this is safe to retry."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=b"")

    result = await _provider(handler).submit_debit(
        idem_key=f"k{status}", amount_paise=1, mandate_id="t", request={}
    )
    assert result.outcome is Outcome.RETRIABLE
    assert result.outcome not in HOLDS_BUDGET


@pytest.mark.asyncio
async def test_rejects_non_positive_amounts() -> None:
    """Money is integer paise and a debit is strictly positive."""

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - never called
        return _json(200, {})

    for amount in (0, -1):
        with pytest.raises(ValueError):
            await _provider(handler).submit_debit(
                idem_key="k", amount_paise=amount, mandate_id="t", request={}
            )


# ── §31: the idempotency key is enforced, not decorative ─────────────────────


@pytest.mark.asyncio
async def test_idempotency_key_travels_as_receipt() -> None:
    """`receipt` is unique per order server side; `notes` is not.

    Putting the key in `notes` would look equivalent and silently permit a
    duplicate charge, so assert on the field that Razorpay actually enforces.
    """
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return _json(200, {"id": "pay_1", "status": "captured"})

    await _provider(handler).submit_debit(
        idem_key="idem-abc-123", amount_paise=1, mandate_id="token_9", request={}
    )
    assert seen["receipt"] == "idem-abc-123"
    assert seen["amount"] == 1  # paise, not rupees
    assert seen["currency"] == "INR"
    assert seen["recurring"] == "1"


# ── reconciliation queries, and never charges ────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_by_key_only_ever_issues_a_get() -> None:
    """§31 reconciles by *querying*. A re-submission would be a second debit."""
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        return _json(200, {"items": []})

    await _provider(handler).fetch_by_key("k")
    assert methods == ["GET"]


@pytest.mark.asyncio
async def test_no_record_means_the_debit_did_not_happen() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(200, {"items": [], "count": 0})

    assert await _provider(handler).fetch_by_key("k") is None


@pytest.mark.asyncio
async def test_paid_order_reconciles_to_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(200, {"items": [{"id": "order_7", "status": "paid"}]})

    result = await _provider(handler).fetch_by_key("k")
    assert result is not None
    assert result.outcome is Outcome.ACCEPTED
    assert result.provider_ref == "order_7"


@pytest.mark.asyncio
async def test_created_but_never_attempted_reconciles_to_not_charged() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json(200, {"items": [{"id": "order_8", "status": "created"}]})

    result = await _provider(handler).fetch_by_key("k")
    assert result is not None
    assert result.outcome is Outcome.DECLINED
    assert result.decline_code == "reconciled_not_charged"


@pytest.mark.asyncio
async def test_in_flight_order_stays_ambiguous() -> None:
    """`attempted` genuinely means unresolved. Do not guess."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json(200, {"items": [{"id": "order_9", "status": "attempted"}]})

    result = await _provider(handler).fetch_by_key("k")
    assert result is not None
    assert result.outcome is Outcome.AMBIGUOUS


@pytest.mark.asyncio
async def test_a_failed_query_leaves_the_slot_held() -> None:
    """The query failing tells us nothing new, so nothing may be released."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    result = await _provider(handler).fetch_by_key("k")
    assert result is not None
    assert result.outcome is Outcome.AMBIGUOUS


# ── recorded from the live test API, 2026-09-01 ──────────────────────────────
#
# `_source: recorded` — this envelope was captured from api.razorpay.com with a
# working test key, not written from the documentation (ADR-090). It is the
# evidence for the mapping below, and the reason that mapping changed.

RECORDED_MALFORMED_REQUEST: Final[dict[str, Any]] = {
    "error": {
        "code": "BAD_REQUEST_ERROR",
        "description": "does_not_exist is not a valid id",
        "source": "internal",
        "step": "payment_initiation",
        "reason": "input_validation_failed",
        "metadata": {},
    }
}


@pytest.mark.asyncio
async def test_our_malformed_request_is_not_recorded_as_a_customer_decline() -> None:
    """The live API reuses `BAD_REQUEST_ERROR` for our bugs and real declines.

    Mapping this to DECLINED would charge the customer's failure statistics for
    our defect: §21's hazard model learns from decline codes, and the cycle
    would carry a refusal the bank never issued. `source: internal` is what
    separates them.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return _json(400, RECORDED_MALFORMED_REQUEST)

    result = await _provider(handler).submit_debit(
        idem_key="k-bug", amount_paise=100, mandate_id="token_does_not_exist", request={}
    )
    assert result.outcome is Outcome.RETRIABLE
    # The point of the test: no decline is attributed to the customer, and no
    # budget slot is consumed for what was our own malformed request.
    assert result.decline_code is None
    assert result.outcome not in HOLDS_BUDGET  # and no slot consumed


@pytest.mark.asyncio
async def test_a_genuine_bank_decline_is_still_declined() -> None:
    """The fix must not swallow real declines — same code, different source."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json(
            400,
            {
                "error": {
                    "code": "BAD_REQUEST_ERROR",
                    "description": "Your account has insufficient balance",
                    "source": "bank",
                    "step": "payment_authorization",
                    "reason": "insufficient_funds",
                }
            },
        )

    result = await _provider(handler).submit_debit(
        idem_key="k-decline", amount_paise=100, mandate_id="token_real", request={}
    )
    assert result.outcome is Outcome.DECLINED
    assert result.decline_code == "insufficient_funds"
