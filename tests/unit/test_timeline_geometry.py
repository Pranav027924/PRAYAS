"""Timeline geometry (Demo spec §R4.2).

The bands are the argument. Saying "NPCI restricts execution windows" is a
claim; showing the debit sitting inside one is evidence — so what these assert
is that the drawing stays faithful to the data rather than that it looks nice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from prayas.console.timeline import WIDTH, budget_pips, build


def _payload(**over: object) -> dict[str, object]:
    start = datetime(2026, 9, 3, 0, 0, tzinfo=UTC)
    base: dict[str, object] = {
        "axis": {"from": start.isoformat(), "to": (start + timedelta(days=2)).isoformat()},
        "windows": [
            {
                "from": (start + timedelta(hours=4)).isoformat(),
                "to": (start + timedelta(hours=8)).isoformat(),
            }
        ],
        "events": [
            {
                "at": (start + timedelta(hours=6)).isoformat(),
                "kind": "pdn_notice",
                "label": "notice",
                "decision_id": "d1",
            },
            {
                "at": (start + timedelta(hours=31)).isoformat(),
                "kind": "debit_attempt",
                "label": "debit",
                "decision_id": "d2",
            },
        ],
    }
    base.update(over)
    return base


def test_markers_land_inside_the_frame_in_time_order() -> None:
    g = build(_payload())
    assert len(g.markers) == 2
    assert g.markers[0].x < g.markers[1].x
    for m in g.markers:
        assert 0 <= m.x <= WIDTH


def test_the_notice_and_debit_are_located_for_the_floor_marker() -> None:
    """The 24-hour floor is drawn between them, so both need coordinates."""
    g = build(_payload())
    assert g.notice_x is not None and g.debit_x is not None
    assert g.notice_x < g.debit_x


def test_an_event_before_the_axis_is_dropped_rather_than_clamped() -> None:
    """Clamping put a month-old failure next to this morning's debit.

    Drawing it at the frame edge implies the two were adjacent, which is a
    stronger claim than the picture is entitled to make. The screen says how
    many fell outside instead.
    """
    payload = _payload()
    events: list[dict[str, object]] = [
        {
            "at": (datetime(2026, 9, 3, tzinfo=UTC) - timedelta(days=30)).isoformat(),
            "kind": "payment_failed",
            "label": "old failure",
            "decision_id": None,
        },
        *(payload["events"] if isinstance(payload["events"], list) else []),
    ]
    payload["events"] = events

    g = build(payload)
    assert len(g.markers) == 2, "an off-axis event was drawn anyway"
    assert all(m.kind != "payment_failed" for m in g.markers)


def test_bands_are_clipped_to_the_frame() -> None:
    """A window running past the axis must not paint outside it."""
    start = datetime(2026, 9, 3, tzinfo=UTC)
    g = build(
        _payload(
            windows=[
                {
                    "from": (start - timedelta(days=1)).isoformat(),
                    "to": (start + timedelta(days=5)).isoformat(),
                }
            ]
        )
    )
    assert g.bands
    for b in g.bands:
        assert b.x >= 0
        assert b.x + b.width <= WIDTH + 0.01


def test_an_empty_or_broken_axis_draws_nothing_rather_than_raising() -> None:
    """A screen is not the place to discover a malformed payload."""
    assert build({}).markers == []
    assert build({"axis": {"from": "nonsense", "to": "also nonsense"}}).bands == []


def test_the_budget_bar_depletes() -> None:
    """§1: one execution plus up to three retries, then the cycle is over.

    Scarcity is the thesis, so the budget is shown depleting rather than
    written as a fraction.
    """
    assert budget_pips(0, 4) == ["left"] * 4
    assert budget_pips(2, 4) == ["used", "used", "left", "left"]
    assert budget_pips(4, 4) == ["used"] * 4


def test_the_bar_never_shows_more_used_than_exists() -> None:
    """A cycle whose attempts exceed its budget is a bug upstream; the bar
    should not compound it by rendering five pips out of four."""
    assert budget_pips(9, 4) == ["used"] * 4
    assert budget_pips(-1, 4) == ["left"] * 4
    assert budget_pips(1, 0) == []
