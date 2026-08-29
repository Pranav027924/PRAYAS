"""Issuer nowcast: is this issuer down right now, and when does it come back?

Master Spec §20 names `issuer_wilson_lower` and `peer_success_rate` as the
features that rule an issuer-side cause in or out. This module computes them
from the live attempt stream, and adds the two things a sequencer actually
needs: *when* the issuer went down, and *when it is likely to be back*.

**Why a Wilson lower bound rather than a success rate.** Three failures out of
three is a success rate of zero, and so is three hundred out of three hundred.
Acting identically on both is how a system declares a national outage because
one merchant retried a dead card twice. The Wilson interval's lower bound
folds sample size into the number itself, so a thin window cannot raise an
alarm on its own — the bound stays low-confidence-wide and never crosses the
threshold.

**Why CUSUM rather than a threshold on the current window.** A threshold on
each window independently either fires on noise or lags badly. CUSUM
accumulates evidence: small deviations in the same direction add up and trip
quickly, while a single bad window decays away. That is the difference between
detecting an outage in one window and detecting it in ten.

**This is an advisory signal, not the compliance gate.** It changes *when* the
sequencer would like to act, never *whether* an action is permitted — Invariant
1 and 2 are untouched, and nothing here can authorise a debit. With no
evidence it reports `UNKNOWN` rather than guessing in either direction, and the
caller decides; a nowcast that silently defaults to "healthy" would quietly
license retries into an outage.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

#: §20 — "other customers, same issuer, same 5-min window".
WINDOW: Final = timedelta(minutes=5)

#: z for a 95% interval. One-sided in use: only the lower bound is consulted.
Z_95: Final = 1.959963984540054

#: Fraction of its own baseline an issuer must fall below before the detector
#: will call it degraded.
#:
#: **Relative, not absolute, and that is the whole point.** An absolute
#: threshold has to be chosen against some assumed healthy rate; get it wrong
#: and the detector is either blind or permanently alarmed. A recurring-debit
#: book clearing 44% and a card portfolio clearing 97% are both healthy, and
#: neither is "below 0.85". Setting the bar at 70% of whatever this issuer
#: normally delivers gives a wide margin in both directions, which is why a
#: single noisy window can no longer flip the verdict.
DEGRADED_FRACTION: Final = 0.70

#: The health index below which `prayas.inference.cause` rules an issuer-side
#: cause in. Applies to the *normalised* index `peer_context` returns, not to a
#: raw success rate — see that function.
#:
#: **The test is on the interval's UPPER bound, not its lower one.** The
#: question a detector must answer is "am I confident the rate is below this",
#: and that is what `upper < threshold` says. Testing the lower bound instead
#: asks "could the rate be below this", which is true almost always: at 40
#: attempts a perfectly healthy 86% issuer has a lower bound near 0.72, so a
#: lower-bound test declares a permanent outage and never sees a recovery.
UNHEALTHY_BELOW: Final = 0.85

#: CUSUM slack, as a fraction of the degradation threshold. Deviations smaller
#: than this are noise and are not accumulated, so an ordinary run of bad luck
#: decays instead of drifting toward an alarm.
CUSUM_SLACK: Final = 0.10

#: CUSUM decision threshold, on the same normalised scale. Crossing it declares
#: a change point.
#:
#: **Both are fractions of the gap, not absolute probabilities.** The size of
#: the signal an outage produces scales with the issuer's baseline: the same
#: collapse to near-zero is a drop of 0.9 for a card portfolio and 0.4 for a
#: recurring-debit book. Absolute constants tuned on one are blind on the other.
CUSUM_THRESHOLD: Final = 0.25

#: Windows a recovered issuer must hold before the outage is declared over.
#: §40.2's exit criterion allows three; requiring two makes the margin real
#: without waiting long enough to matter.
RECOVERY_WINDOWS: Final = 2

#: Closed windows retained per issuer — 24 hours at five minutes.
_CLOSED_HISTORY: Final = 288

#: Span `peer_context` aggregates over when asking "what were this issuer's
#: peers doing". §20 specifies five minutes, which assumes production volume:
#: at a few transactions per issuer-minute a five-minute bucket is usually
#: empty, and where it is not, a Wilson bound over five attempts is noise. The
#: window is widened until the evidence is worth reading — the alternative is
#: not a sharper signal but a confident wrong one.
PEER_SPAN: Final = timedelta(minutes=60)

HEALTHY: Final = "healthy"
DEGRADED: Final = "degraded"
UNKNOWN: Final = "unknown"


def wilson_interval(successes: int, attempts: int, *, z: float = Z_95) -> tuple[float, float]:
    """The Wilson score interval for a success rate.

    Returns `(0.0, 1.0)` for an empty sample — maximal ignorance, which is the
    honest answer and the one that cannot trip a threshold in either direction.
    Callers distinguish "no data" from "genuinely bad" via `NowcastState.status`,
    which reports `UNKNOWN` rather than letting a zero bound masquerade as an
    outage.
    """
    if attempts < 0 or successes < 0:
        raise ValueError("counts must be non-negative")
    if successes > attempts:
        raise ValueError(f"successes ({successes}) exceeds attempts ({attempts})")
    if attempts == 0:
        return 0.0, 1.0

    n = float(attempts)
    p = successes / n
    z2 = z * z
    denominator = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denominator
    margin = (z / denominator) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def wilson_lower_bound(successes: int, attempts: int, *, z: float = Z_95) -> float:
    """§20's `issuer_wilson_lower`, reported as a feature."""
    return wilson_interval(successes, attempts, z=z)[0]


def window_start(at: datetime, *, window: timedelta = WINDOW) -> datetime:
    """Floor a timestamp to its window boundary, so all peers share buckets."""
    if at.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware; times are stored UTC")
    at = at.astimezone(UTC)
    seconds = int(window.total_seconds())
    epoch = int(at.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=UTC)


@dataclass(slots=True)
class _Window:
    attempts: int = 0
    successes: int = 0


@dataclass(frozen=True, slots=True)
class NowcastState:
    """What the nowcast believes about one issuer at one moment."""

    issuer: str
    at: datetime
    status: str
    wilson_lower: float
    attempts: int
    successes: int
    #: When the current outage was first declared, if any.
    degraded_since: datetime | None
    #: Best estimate of when the issuer comes back, if degraded.
    recovery_eta: datetime | None

    @property
    def is_degraded(self) -> bool:
        return self.status == DEGRADED

    @property
    def peer_success_rate(self) -> float:
        """§20's feature: the raw rate among peers in this window.

        Reported alongside the Wilson bound rather than instead of it, because
        §20 lists both — the point estimate says what happened, the bound says
        how much of it to believe.
        """
        return self.successes / self.attempts if self.attempts else 0.0


@dataclass(slots=True)
class _IssuerTracker:
    windows: dict[datetime, _Window] = field(default_factory=dict)
    cusum_down: float = 0.0
    cusum_up: float = 0.0
    degraded_since: datetime | None = None
    healthy_streak: int = 0
    last_window: datetime | None = None
    outage_history: list[float] = field(default_factory=list)
    #: Closed windows, kept as `(start, attempts, successes)` so a trailing
    #: query can aggregate several of them. Bounded: this is a live detector,
    #: not a warehouse.
    closed: deque[tuple[datetime, int, int]] = field(
        default_factory=lambda: deque(maxlen=_CLOSED_HISTORY)
    )


class IssuerNowcast:
    """Streaming outage detector over per-issuer attempt outcomes.

    Fed one attempt at a time via `observe`, in roughly chronological order.
    `state` answers for any issuer at any moment; it never mutates, so a caller
    can consult it inside a decision without changing what the next caller sees.
    """

    def __init__(
        self,
        *,
        baseline_success: float = 0.82,
        window: timedelta = WINDOW,
        degraded_fraction: float = DEGRADED_FRACTION,
        cusum_slack: float = CUSUM_SLACK,
        cusum_threshold: float = CUSUM_THRESHOLD,
        min_attempts: int = 5,
        expected_outage_minutes: float = 54.0,
    ) -> None:
        if not 0.0 < baseline_success <= 1.0:
            raise ValueError("baseline_success must be in (0, 1]")
        if min_attempts < 1:
            raise ValueError("min_attempts must be at least 1")
        if expected_outage_minutes <= 0:
            raise ValueError("expected_outage_minutes must be positive")

        if not 0.0 < degraded_fraction < 1.0:
            raise ValueError("degraded_fraction must be in (0, 1)")

        self.baseline = baseline_success
        self.window = window
        #: The absolute success rate this issuer must fall below to be called
        #: degraded, derived from its own baseline.
        self.degraded_below = degraded_fraction * baseline_success
        self.slack = cusum_slack
        self.threshold = cusum_threshold
        self.min_attempts = min_attempts
        self.expected_outage = timedelta(minutes=expected_outage_minutes)
        self._issuers: dict[str, _IssuerTracker] = {}

    def observe(self, issuer: str, at: datetime, *, success: bool) -> None:
        """Record one attempt outcome, and close any window it advances past."""
        tracker = self._issuers.setdefault(issuer, _IssuerTracker())
        start = window_start(at, window=self.window)

        if tracker.last_window is not None and start > tracker.last_window:
            self._close_through(tracker, up_to=start)

        bucket = tracker.windows.setdefault(start, _Window())
        bucket.attempts += 1
        bucket.successes += int(success)
        tracker.last_window = (
            start if tracker.last_window is None else max(tracker.last_window, start)
        )

    def _close_through(self, tracker: _IssuerTracker, *, up_to: datetime) -> None:
        """Run the CUSUM over every completed window before `up_to`.

        Empty windows are skipped rather than scored as zero success: an issuer
        nobody happened to attempt against is not an issuer that failed.
        """
        pending = sorted(w for w in tracker.windows if w < up_to)
        for start in pending:
            bucket = tracker.windows[start]
            if bucket.attempts >= self.min_attempts:
                self._step(tracker, start, bucket)
            tracker.closed.append((start, bucket.attempts, bucket.successes))
            del tracker.windows[start]

    def _confidently_degraded(self, successes: int, attempts: int) -> bool:
        """Is the whole 95% interval below the unhealthy threshold?

        This is the detector's one decision statistic. `upper < threshold` says
        "I am confident the rate is below this"; the lower-bound version of the
        same test says "the rate could be below this", which is true of a
        perfectly healthy issuer at any realistic window size.
        """
        _, upper = wilson_interval(successes, attempts)
        return attempts >= self.min_attempts and upper < self.degraded_below

    def _step(self, tracker: _IssuerTracker, start: datetime, bucket: _Window) -> None:
        _, upper = wilson_interval(bucket.successes, bucket.attempts)
        degraded_now = self._confidently_degraded(bucket.successes, bucket.attempts)

        # CUSUM over the *normalised* distance from the threshold, so one unit
        # means "as far below the line as the line is above zero" whatever the
        # issuer's baseline. Confidently bad windows push `down` up, clearly
        # fine ones push it back toward zero. Both are floored so evidence in
        # one direction never becomes a debt owed by the other — which is what
        # lets a single unlucky window decay away instead of counting toward an
        # alarm forever.
        deviation = (self.degraded_below - upper) / self.degraded_below
        tracker.cusum_down = max(0.0, tracker.cusum_down + deviation - self.slack)
        tracker.cusum_up = max(0.0, tracker.cusum_up - deviation - self.slack)

        if tracker.degraded_since is None:
            if tracker.cusum_down > self.threshold and degraded_now:
                tracker.degraded_since = start
                tracker.cusum_up = 0.0
                tracker.healthy_streak = 0
        elif degraded_now:
            # Decrement rather than reset. A single unlucky window inside an
            # otherwise recovered stream is common — at forty attempts against
            # an 86% rate, roughly one window in fifty clears the
            # confident-degradation test by chance — and restarting the streak
            # from zero turns that into two extra windows of latency. Losing
            # one window instead bounds recovery at three, while a genuine
            # outage still pins the streak at zero because *every* window is
            # confidently degraded.
            tracker.healthy_streak = max(0, tracker.healthy_streak - 1)
        else:
            tracker.healthy_streak += 1
            if tracker.healthy_streak >= RECOVERY_WINDOWS:
                duration = (start - tracker.degraded_since).total_seconds() / 60.0
                tracker.outage_history.append(duration)
                tracker.degraded_since = None
                tracker.cusum_down = 0.0
                tracker.healthy_streak = 0

    def flush(self, up_to: datetime) -> None:
        """Close every window before `up_to`, for all issuers.

        Needed because detection happens when a window *completes*; without a
        flush the final window of a stream is never scored, and a test that
        injects an outage at the end would see nothing.
        """
        boundary = window_start(up_to, window=self.window)
        for tracker in self._issuers.values():
            self._close_through(tracker, up_to=boundary)

    def trailing_counts(
        self, issuer: str, at: datetime, *, span: timedelta = PEER_SPAN
    ) -> tuple[int, int]:
        """`(successes, attempts)` for `issuer` over the `span` ending at `at`.

        Includes the still-filling current window, because a caller asking
        "what is happening right now" wants the most recent evidence, not the
        most recent *complete* evidence.
        """
        tracker = self._issuers.get(issuer)
        if tracker is None:
            return 0, 0

        floor = at - span
        successes = attempts = 0
        for start, window_attempts, window_successes in tracker.closed:
            if start >= floor:
                attempts += window_attempts
                successes += window_successes
        for start, bucket in tracker.windows.items():
            if start >= floor:
                attempts += bucket.attempts
                successes += bucket.successes
        return successes, attempts

    def recovery_eta(self, issuer: str, at: datetime) -> datetime | None:
        """When the issuer is expected back, or None if it is not down.

        Outage durations are heavy-tailed (§38 draws them LogNormal), so the
        estimate is the *conditional* expected remaining time given the outage
        has already lasted this long — not a fixed offset from onset. An outage
        that has run long is evidence it will run longer, and an ETA that
        ignored that would keep promising a recovery that never arrives.
        """
        tracker = self._issuers.get(issuer)
        if tracker is None or tracker.degraded_since is None:
            return None

        elapsed = max((at - tracker.degraded_since).total_seconds() / 60.0, 0.0)
        observed = tracker.outage_history
        mean = (
            sum(observed) / len(observed)
            if observed
            else self.expected_outage.total_seconds() / 60.0
        )

        # Memoryless remaining-time approximation: for a heavy-tailed duration
        # the expected remainder does not shrink toward zero as the outage runs
        # on, so the floor is a fraction of the mean rather than zero.
        remaining = max(mean - elapsed, 0.25 * mean)
        return at + timedelta(minutes=remaining)

    def state(self, issuer: str, at: datetime) -> NowcastState:
        """The current belief about `issuer`, without advancing anything."""
        tracker = self._issuers.get(issuer)
        if tracker is None:
            return NowcastState(
                issuer=issuer,
                at=at,
                status=UNKNOWN,
                wilson_lower=0.0,
                attempts=0,
                successes=0,
                degraded_since=None,
                recovery_eta=None,
            )

        start = window_start(at, window=self.window)
        bucket = tracker.windows.get(start, _Window())
        lower = wilson_lower_bound(bucket.successes, bucket.attempts)

        # **Only the CUSUM declares an outage.** The live window is still
        # filling, and judging it on its own is exactly the per-window
        # false-alarm the change-point detector exists to avoid: at eight
        # attempts an ordinary run of bad luck clears the confident-degradation
        # test, and a healthy issuer would be declared down several times an
        # hour. The live bucket is still reported, so a caller can see what is
        # happening now — it just does not get a vote.
        if tracker.degraded_since is not None:
            status = DEGRADED
        elif bucket.attempts < self.min_attempts:
            status = UNKNOWN
        else:
            status = HEALTHY

        return NowcastState(
            issuer=issuer,
            at=at,
            status=status,
            wilson_lower=lower,
            attempts=bucket.attempts,
            successes=bucket.successes,
            degraded_since=tracker.degraded_since,
            recovery_eta=self.recovery_eta(issuer, at),
        )

    def degraded_issuers(self) -> dict[str, datetime]:
        """Every issuer currently believed down, and since when."""
        return {
            issuer: tracker.degraded_since
            for issuer, tracker in self._issuers.items()
            if tracker.degraded_since is not None
        }


def peer_context(
    nowcast: IssuerNowcast, issuer: str, at: datetime, *, span: timedelta = PEER_SPAN
) -> tuple[float, float]:
    """§20's `(issuer_wilson_lower, peer_success_rate)` pair for a failure.

    Two corrections separate this from a naive read of the current bucket, and
    without either one the heuristic in `prayas.inference.cause` calls every
    decline an outage:

    **Aggregate over `span`, not one 5-minute bucket.** §20's five minutes
    assumes production volume. Below that, most buckets are empty and the rest
    carry a handful of attempts, where a Wilson bound is nearly vacuous.

    **Normalise against the issuer's healthy baseline.** `cause` tests
    `issuer_wilson_lower < 0.85`. A raw lower bound sits under 0.85 for a
    perfectly healthy issuer at any realistic sample size — at forty attempts
    against an 82% baseline it is near 0.70. Dividing by the baseline turns it
    into "how much of its normal success is this issuer delivering", which is
    the quantity that threshold was always describing.

    Returns the neutral `(1.0, 1.0)` when the evidence is too thin to read,
    which is what `CauseContext`'s defaults mean: nothing here points at the
    issuer. Silence is not evidence of an outage.
    """
    successes, attempts = nowcast.trailing_counts(issuer, at, span=span)
    if attempts < nowcast.min_attempts:
        return 1.0, 1.0

    lower = wilson_lower_bound(successes, attempts)
    health = min(1.0, lower / nowcast.baseline)
    peer = min(1.0, (successes / attempts) / nowcast.baseline)
    return health, peer
