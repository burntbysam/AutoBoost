"""Tests for run-on-selected-parts (0.7.24).

The operator multi-selects parts in Boost's Home list (Ctrl/Shift-click,
scattered singles and small groups) and AutoBoost runs exactly those --
no typing out fifty part numbers. selected_parts() sweeps the virtualized
list reading each realized row's SelectionItemPattern; these tests drive the
collection logic through a stubbed _walk_list, so the dedup/order/diagnostic
behaviour is locked down without a live window.

Runnable with pytest or directly:  python -m tests.test_selected_parts
"""

from __future__ import annotations

from autoboost.navigator.boost_uia import BoostUIA, _item_selected


class _Item:
    """Fake ListItem: sel=True/False mimics the SelectionItemPattern; sel=None
    mimics a row whose pattern can't be read at all."""

    def __init__(self, sel):
        self._sel = sel

    def is_selected(self):
        if self._sel is None:
            raise RuntimeError("no SelectionItemPattern")
        return self._sel

    @property
    def iface_selection_item(self):
        raise RuntimeError("no SelectionItemPattern")


class _IfaceOnlyItem:
    """Wrapper whose is_selected() is missing but whose raw pattern works --
    exercises _item_selected's fallback."""

    def is_selected(self):
        raise AttributeError("wrapper api missing")

    class _Pattern:
        CurrentIsSelected = True

    iface_selection_item = _Pattern()


def _row(name, sel):
    return {"name": name, "raw": None, "item": _Item(sel)}


def _stub_uia(stops):
    """A BoostUIA whose _walk_list serves canned row batches (one per sweep
    stop, overlapping like the real virtualized list) -- no pywinauto needed."""
    b = object.__new__(BoostUIA)
    b.last_scroll_info = ""
    b.last_value = ""

    def walk(visit):
        for rows in stops:
            if visit(rows):
                return

    b._walk_list = walk
    return b


def test_scattered_singles_and_groups_in_order():
    stops = [
        [_row("A", True), _row("B", False), _row("C", True)],
        [_row("B", False), _row("C", True), _row("D", True), _row("E", False)],
        [_row("E", False), _row("F", True)],
    ]
    got = [p["name"] for p in _stub_uia(stops).selected_parts()]
    # C is realized at two overlapping stops -- collected once, order kept.
    assert got == ["A", "C", "D", "F"]


def test_nothing_selected_is_empty_with_summary():
    b = _stub_uia([[_row("A", False), _row("B", False)]])
    assert b.selected_parts() == []
    assert "0 selected" in b.last_value


def test_unreadable_rows_are_counted_not_selected():
    b = _stub_uia([[_row("A", None), _row("B", True), _row("C", None)]])
    got = [p["name"] for p in b.selected_parts()]
    assert got == ["B"]
    assert "2 unreadable" in b.last_value


def test_item_selected_states():
    assert _item_selected(_Item(True)) is True
    assert _item_selected(_Item(False)) is False
    assert _item_selected(_Item(None)) is None          # pattern unreadable
    assert _item_selected(_IfaceOnlyItem()) is True     # raw-pattern fallback


def test_parts_still_collects_everything():
    # The plain enumeration ignores selection entirely.
    stops = [
        [_row("A", True), _row("B", False)],
        [_row("B", False), _row("C", None)],
    ]
    got = [p["name"] for p in _stub_uia(stops).parts()]
    assert got == ["A", "B", "C"]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
