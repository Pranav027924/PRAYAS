"""Geometry for the cycle timeline (Demo spec §R4.2).

Hand-rolled inline SVG. Three visualisations in the whole demo — a charting
library costs more setup than it saves, and every one of them wants a CDN (N3).

**Computed server-side, from the same adapter the DP solved against.** The
shaded bands are `RailAdapter.is_execution_legal` rendered, not a restatement
of it, so the picture and the mask cannot drift apart. That matters more than
it sounds: the bands are the argument. Saying "NPCI restricts execution
windows" is a claim; showing the debit sitting inside one is evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from prayas.console.format import IST

#: Drawing box. Wide enough that a 3-day episode still separates its markers.
WIDTH = 980
HEIGHT = 132
PAD_L = 16
PAD_R = 16
AXIS_Y = 92
BAND_TOP = 30
BAND_H = 34


@dataclass(frozen=True, slots=True)
class Band:
    x: float
    width: float
    label: str


@dataclass(frozen=True, slots=True)
class Marker:
    x: float
    at: datetime
    kind: str
    label: str
    decision_id: str | None
    verdict: str | None
    #: Markers are staggered so two events an hour apart do not overprint.
    row: int
    #: A scheduled action that has not fired yet (Demo spec Phase 6) — drawn
    #: ghosted rather than lit, distinguishing "will happen" from "happened".
    pending: bool = False


@dataclass(frozen=True, slots=True)
class Tick:
    x: float
    label: str


@dataclass(frozen=True, slots=True)
class Geometry:
    bands: list[Band]
    markers: list[Marker]
    ticks: list[Tick]
    notice_x: float | None
    debit_x: float | None
    #: Where "now" sits on the axis (Demo spec Phase 6's playhead). `None`
    #: when the payload carries no `now` — an older caller, or a frame with no
    #: valid axis at all.
    now_x: float | None = None
    width: int = WIDTH
    height: int = HEIGHT
    axis_y: int = AXIS_Y
    band_top: int = BAND_TOP
    band_h: int = BAND_H


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def build(payload: dict[str, Any]) -> Geometry:
    """Turn a `/v1/cycles/{id}/timeline` payload into drawing coordinates."""
    axis = payload.get("axis") or {}
    start = _parse(axis.get("from"))
    end = _parse(axis.get("to"))
    if start is None or end is None or end <= start:
        return Geometry(bands=[], markers=[], ticks=[], notice_x=None, debit_x=None, now_x=None)

    span = (end - start).total_seconds()
    inner = WIDTH - PAD_L - PAD_R

    def x_of(moment: datetime) -> float:
        return PAD_L + inner * ((moment - start).total_seconds() / span)

    bands: list[Band] = []
    for window in payload.get("windows", []):
        w_from, w_to = _parse(window.get("from")), _parse(window.get("to"))
        if w_from is None or w_to is None:
            continue
        left = max(x_of(w_from), PAD_L)
        right = min(x_of(w_to), WIDTH - PAD_R)
        if right - left > 0.5:
            bands.append(Band(x=left, width=right - left, label=""))

    markers: list[Marker] = []
    notice_x: float | None = None
    debit_x: float | None = None
    last_x = -999.0
    row = 0
    for event in payload.get("events", []):
        at = _parse(event.get("at"))
        if at is None:
            continue
        if at < start:
            # Off-axis: drawing it at the frame edge would put an event a month
            # old next to one from this morning and imply they were adjacent.
            continue
        x = x_of(at)
        # Stagger only when two markers would collide; a fixed alternation
        # makes an evenly spaced timeline look like it has structure it lacks.
        row = (row + 1) % 3 if x - last_x < 96 else 0
        last_x = x
        kind = str(event.get("kind", ""))
        if kind == "pdn_notice":
            notice_x = x
        if kind == "debit_attempt":
            debit_x = x
        markers.append(
            Marker(
                x=x,
                at=at,
                kind=kind,
                label=str(event.get("label") or kind),
                decision_id=event.get("decision_id"),
                verdict=event.get("verdict"),
                row=row,
                pending=bool(event.get("pending", False)),
            )
        )

    ticks: list[Tick] = []
    cursor = start.astimezone(IST).replace(minute=0, second=0, microsecond=0)
    step = timedelta(hours=6 if span <= 4 * 86400 else 24)
    while cursor < end:
        if cursor >= start:
            ticks.append(Tick(x=x_of(cursor), label=cursor.strftime("%d %b %H:%M")))
        cursor += step

    now = _parse(payload.get("now"))
    now_x = x_of(now) if now is not None and start <= now <= end else None

    return Geometry(
        bands=bands, markers=markers, ticks=ticks, notice_x=notice_x, debit_x=debit_x, now_x=now_x
    )


def budget_pips(used: int, total: int) -> list[str]:
    """§1's attempt budget as filled and empty pips.

    Scarcity is the whole thesis — one execution plus up to three retries, and
    then the cycle is over — so the budget is drawn depleting rather than
    written as a fraction.
    """
    total = max(int(total), 0)
    used = min(max(int(used), 0), total)
    return ["used"] * used + ["left"] * (total - used)
