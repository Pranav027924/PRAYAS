"""Formatting for the demo surface (Demo spec N6).

Money is integer paise everywhere in the engine and is formatted only here, at
the edge. Nothing in this module is arithmetic: `rupees()` returns a string and
there is deliberately no way to get a float back out of it.

**Indian grouping, not thousands.** ₹24,86,400 — the last three digits, then
pairs. A payments audience in India reads `₹2,486,400` as a mistake, and it is
one: the digit groups carry lakh and crore, which is how the amount is spoken.
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo

IST: tzinfo = ZoneInfo("Asia/Kolkata")


def _group_indian(digits: str) -> str:
    """Last three digits, then pairs: 2486400 -> 24,86,400."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join([*parts, tail])


def rupees(paise: object, *, decimals: bool = False) -> str:
    """Paise to a rupee string with Indian grouping.

    `decimals=False` by default: a portfolio total reads ₹18,42,300, and the
    paise on a figure that size are noise. A single cycle's amount asks for
    them.
    """
    try:
        value = int(paise)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value)
    whole, frac = divmod(value, 100)
    body = _group_indian(str(whole))
    return f"{sign}₹{body}.{frac:02d}" if decimals else f"{sign}₹{body}"


def lakh(paise: object) -> str:
    """A compact form for axis labels and captions: ₹14.1L, ₹1.2Cr."""
    try:
        value = int(paise)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return "—"
    rupee = abs(value) / 100
    sign = "-" if value < 0 else ""
    if rupee >= 1_00_00_000:
        return f"{sign}₹{rupee / 1_00_00_000:.1f}Cr"
    if rupee >= 1_00_000:
        return f"{sign}₹{rupee / 1_00_000:.1f}L"
    if rupee >= 1_000:
        return f"{sign}₹{rupee / 1_000:.1f}K"
    return f"{sign}₹{rupee:.0f}"


def pct(value: object, *, digits: int = 1, signed: bool = False) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return f"{number:+.{digits}f}%" if signed else f"{number:.{digits}f}%"


def ist(moment: datetime | str | None, fmt: str = "%d %b %H:%M") -> str:
    """Render in IST and say so where it matters.

    §R2 returns UTC. Regulatory windows are IST-defined, so a screen showing
    UTC asks its audience to do arithmetic before they can tell whether a
    debit was lawful.
    """
    if moment is None:
        return "—"
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return "—"
    return moment.astimezone(IST).strftime(fmt)


def hours(delta: object) -> str:
    try:
        value = float(delta)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "—"
    return f"{value:.0f}h"


def ago(moment: datetime | str | None, *, now: datetime | None = None) -> str:
    """Relative time for the live ticker."""
    if moment is None:
        return "—"
    if isinstance(moment, str):
        try:
            moment = datetime.fromisoformat(moment)
        except ValueError:
            return "—"
    reference = now or datetime.now(moment.tzinfo)
    seconds = int((reference - moment).total_seconds())
    if seconds < 60:
        return f"{max(seconds, 0)}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


def duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    if total < 3600:
        return f"{total // 60}m"
    return f"{total // 3600}h {total % 3600 // 60:02d}m"
