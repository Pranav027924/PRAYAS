"""Counter and gauge seam (ADR-017).

§43 defines metrics and alert thresholds but names no technology, and §42's SLOs
are set in Phase 15 Hardening. Emitting through the Phase 0 structured-logging
path keeps call sites stable so a backend can be chosen later, once there are
SLOs to inform the choice.

Values ride the redacting formatter, so a label carrying something sensitive is
masked on the way out rather than needing every call site to be careful.
"""

from __future__ import annotations

import logging
from typing import Any, Final

log = logging.getLogger("prayas.metrics")

#: §43 — "Consumer lag (projector) > 30s: stale state risks acting on old reality".
PROJECTOR_LAG_ALERT_SECONDS: Final = 30.0


def increment(name: str, *, amount: int = 1, **labels: Any) -> None:
    """Record a counter increment.

    Emitted at INFO because these are operational signals, not diagnostics — a
    `stale_transition` that only appears at DEBUG is a metric nobody sees.
    """
    log.info("metric.counter", extra={"metric": name, "amount": amount, **labels})


def gauge(name: str, value: float, **labels: Any) -> None:
    """Record a point-in-time value."""
    log.info("metric.gauge", extra={"metric": name, "value": value, **labels})


def projector_lag(seconds: float, *, pending: int) -> None:
    """Report projector consumer lag, warning past §43's threshold."""
    breached = seconds > PROJECTOR_LAG_ALERT_SECONDS
    log.log(
        logging.WARNING if breached else logging.INFO,
        "metric.gauge",
        extra={
            "metric": "projector_lag_seconds",
            "value": seconds,
            "pending": pending,
            "threshold_breached": breached,
        },
    )
