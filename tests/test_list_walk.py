"""Tests for the parts-list sweep math (0.7.22).

The Home parts list is virtualized -- off-screen rows are not in the UIA tree
-- so enumerating 140 parts means physically sweeping the list. The 0.7.21 run
collected only the 13 VISIBLE rows because wheel scrolling silently went
nowhere; the walk now commands positions through the UIA ScrollPattern using
stops from _scroll_positions. Its guarantee: consecutive viewports OVERLAP for
every viewport size, so no band of rows is ever skipped -- a skipped band is a
silently missing part.

A scroll-percent step S moves the viewport top by S*(100-view)/100 percent of
the content; overlap therefore requires S*(100-view)/100 < view. The tests
check that property directly.

Runnable with pytest or directly:  python -m tests.test_list_walk
"""

from __future__ import annotations

from autoboost.navigator.boost_uia import _scroll_positions


def _covers_without_gaps(view: float, stops: list[float]) -> bool:
    """True if sweeping 0 -> stops leaves no unseen band of content."""
    prev = 0.0
    for pos in stops:
        moved = (pos - prev) * (100.0 - view) / 100.0   # content-% the top moved
        if moved >= view:                               # jumped past a viewport
            return False
        prev = pos
    return not stops or stops[-1] >= 100.0              # ended at the bottom


def test_fits_one_viewport_needs_no_scrolling():
    assert _scroll_positions(100) == []
    assert _scroll_positions(99.5) == []
    assert _scroll_positions(1e6) == []


def test_typical_140_part_list():
    # 13 visible of 140 -> viewport ~9.3% of content.
    stops = _scroll_positions(9.3)
    assert stops, "a 140-part list must be swept"
    assert stops[-1] == 100.0
    assert _covers_without_gaps(9.3, stops)
    assert len(stops) < 40, "sweep should stay a few dozen stops, not hundreds"


def test_half_visible():
    stops = _scroll_positions(50)
    assert stops[-1] == 100.0
    assert _covers_without_gaps(50, stops)
    assert len(stops) <= 5


def test_monotonic_increasing_and_bounded():
    for view in (0.5, 1, 5, 9.3, 25, 50, 80, 99):
        stops = _scroll_positions(view)
        assert all(b > a for a, b in zip(stops, stops[1:])), view
        assert all(0 < s <= 100.0 for s in stops), view
        assert _covers_without_gaps(view, stops), view


def test_huge_list_is_bounded():
    # viewport 0.5% ~ a 2600-row list: many stops, but bounded and gap-free.
    stops = _scroll_positions(0.4)   # clamped up to 0.5
    assert stops[-1] == 100.0
    assert len(stops) < 400
    assert _covers_without_gaps(0.5, stops)


def test_garbage_input_still_sane():
    for bad in (None, "x", 0, -5):
        stops = _scroll_positions(bad)
        assert stops == [] or (stops[-1] == 100.0
                               and all(b > a for a, b in zip(stops, stops[1:]))), bad


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
