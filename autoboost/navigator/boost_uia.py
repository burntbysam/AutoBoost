"""UIA driver for TruTops Boost, built from the probe dumps.

This wraps pywinauto to do the reliable, non-canvas parts of the workflow by
control identity (immune to RDP blur / resolution), leaving only the opaque
graphics canvas to vision.

Confirmed available from the probes:
  HomeZone (title "TruTops Boost - HomeZone"):
    - parts list:  List  auto_id='List.ResultList'
        each item: Text auto_id='List.ResultList.<PartName>.Description' (name)
                   Text auto_id='List.ResultList.<PartName>.RawMaterial'
    - toolbar:     Button auto_id='Part.Toolbar.Save' / '.Delete' / '.CalculateAll'
  Design (title "<part> - TruTops Boost - Design"):
    - ribbon buttons by name: 'Save', 'Open', 'Design', '2D Processing', ...
    - Dimensions: a Text 'Dimensions' followed by an Edit holding e.g. "18.2 in x 10 in"
    - property grid: Pane auto_id='propertyGrid1' with named Button rows,
      including 'User-defined' and 'More...'.

Read-only self-test (safe -- clicks nothing):
    python -m autoboost.navigator.boost_uia --selftest

Everything that mutates Boost (select_part, click_property, ...) is a plain
method, not exercised by the self-test.
"""

from __future__ import annotations

import argparse
import sys

HOME_TITLE = "TruTops Boost - HomeZone"
DESIGN_TITLE_RE = r".* - TruTops Boost - Design"


def _text(wrapper) -> str:
    try:
        return wrapper.window_text() or ""
    except Exception:
        return ""


def _auto_id(wrapper) -> str:
    try:
        return wrapper.element_info.automation_id or ""
    except Exception:
        return ""


def _value(wrapper) -> str:
    """Fast best-effort read of an Edit/value control's current value."""
    for attempt in (
        lambda: wrapper.get_value(),
        lambda: wrapper.iface_value.CurrentValue,
        lambda: wrapper.window_text(),
    ):
        try:
            val = attempt()
            if val:
                return val
        except Exception:
            continue
    return ""


class BoostUIA:
    """Thin UIA facade over the two Boost windows."""

    def __init__(self):
        from pywinauto import Desktop  # imported here so import errors are clear
        from pywinauto.timings import Timings
        # The WinForms-UIA bridge is slow; don't let a missing control burn the
        # full default find timeout (5s) on every lookup.
        Timings.window_find_timeout = 2
        self.desktop = Desktop(backend="uia")
        self._home = None
        self._design = None
        self._grid = None
        self.last_value = ""   # last value observed by a set operation (for tests)

    # -- window handles -----------------------------------------------------
    # Window/grid specs are cached: resolving them re-searches the UIA tree,
    # which is the main source of slowness. Call reset() if windows change.

    def reset(self) -> None:
        self._home = self._design = self._grid = None

    def home(self):
        if self._home is None:
            self._home = self.desktop.window(title=HOME_TITLE, control_type="Window")
        return self._home

    def design(self):
        if self._design is None:
            self._design = self.desktop.window(title_re=DESIGN_TITLE_RE, control_type="Window")
        return self._design

    def has_home(self) -> bool:
        try:
            return self.home().exists(timeout=1)
        except Exception:
            return False

    def has_design(self) -> bool:
        try:
            return self.design().exists(timeout=1)
        except Exception:
            return False

    # -- HomeZone: part list ------------------------------------------------

    def parts(self) -> list[dict]:
        """Return [{name, raw, item}] for every part in the HomeZone list."""
        out: list[dict] = []
        home = self.home().wrapper_object()
        for item in home.descendants(control_type="ListItem"):
            if not _auto_id(item) and "ResultList" not in _text(item):
                # ListItems in the result list carry name "Name: <part>, ID: ...".
                pass
            name, raw = None, None
            for t in item.descendants(control_type="Text"):
                aid = _auto_id(t)
                if aid.endswith(".Description"):
                    name = _text(t)
                elif aid.endswith(".RawMaterial"):
                    raw = _text(t)
            if name:
                out.append({"name": name, "raw": raw, "item": item})
        return out

    def select_part(self, name: str) -> bool:
        """Click the part whose Description equals `name`. Returns success."""
        for p in self.parts():
            if p["name"] == name:
                p["item"].click_input()
                return True
        return False

    # -- Design: dimensions & property grid ---------------------------------

    def read_dimensions(self) -> str:
        """Read the Design 'Dimensions' field, e.g. '18.2 in x 10 in'.

        Found positionally: the Edit immediately right of the 'Dimensions' label
        on the same row. Structure-independent so it survives layout changes.
        """
        design = self.design().wrapper_object()
        labels = [t for t in design.descendants(control_type="Text")
                  if _text(t) == "Dimensions"]
        if not labels:
            return ""
        lr = labels[0].rectangle()
        best, best_left = None, None
        for e in design.descendants(control_type="Edit"):
            er = e.rectangle()
            same_row = abs((er.top + er.bottom) // 2 - (lr.top + lr.bottom) // 2) < 20
            to_right = lr.right - 5 <= er.left <= lr.right + 250
            if same_row and to_right and (best_left is None or er.left < best_left):
                best, best_left = e, er.left
        return _value(best) if best is not None else ""

    def _property_grid(self):
        if self._grid is None:
            self._grid = self.design().child_window(auto_id="propertyGrid1")
        return self._grid

    def property_rows(self) -> list[str]:
        """Names of the rows currently in the Design property grid."""
        try:
            grid = self._property_grid().wrapper_object()
        except Exception:
            return []
        return [_text(b) for b in grid.descendants(control_type="Button") if _text(b)]

    def click_property(self, name: str) -> bool:
        """Click a named row/button in the property grid (e.g. 'More...')."""
        try:
            btn = self._property_grid().child_window(title=name, control_type="Button")
            btn.click_input()
            return True
        except Exception:
            return False

    # -- Design: font chain (keyboard-driven; the value list is owner-drawn) --
    #
    # The value combo does single-letter select-and-close: one keystroke jumps
    # to the FIRST item starting with that letter and closes the list. So we
    # can't type the full value. Instead we press the first letter repeatedly to
    # cycle through the matching items (EasyType-L=4mm -> 5mm -> 8mm -> 8.5mm ->
    # 10mm) and stop when the read-back value equals the target.
    #
    # All the controls are fetched with a single descendants() call and filtered
    # in Python -- repeated child_window() lookups against the WinForms-UIA
    # bridge were what made this take a minute.

    _EDITOR_AUTOID = "9765996"     # the WinForms EDIT holding the value
    _OPEN_AUTOID = "4261530"       # its dropdown 'Open' arrow

    def _grid_controls(self) -> dict:
        """One descendants() sweep -> {'buttons': {name: wrapper}, 'edit', 'open'}."""
        grid = self._property_grid().wrapper_object()
        buttons, edit, openbtn = {}, None, None
        for c in grid.descendants():
            aid = _auto_id(c)
            if aid == self._EDITOR_AUTOID:
                edit = c
            elif aid == self._OPEN_AUTOID:
                openbtn = c
            else:
                name = _text(c)
                if name and c.element_info.control_type == "Button":
                    buttons.setdefault(name, c)
        return {"buttons": buttons, "edit": edit, "open": openbtn}

    def property_rows(self) -> list[str]:
        """Names of the rows currently in the Design property grid."""
        try:
            return list(self._grid_controls()["buttons"].keys())
        except Exception:
            return []

    def click_property(self, name: str) -> bool:
        """Click a named row/button in the property grid (e.g. 'More...')."""
        try:
            btn = self._grid_controls()["buttons"].get(name)
            if btn is None:
                return False
            btn.click_input()
            return True
        except Exception:
            return False

    def set_font_type(self, value: str = "EasyType-L=10mm", max_presses: int = 30) -> bool:
        """Set 'Font type' to `value` by cycling the first letter with oracle.

        Records the final committed value in self.last_value.
        """
        import time
        from pywinauto.keyboard import send_keys

        ctrls = self._grid_controls()
        row = ctrls["buttons"].get("Font type")
        if row is None:
            self.last_value = ""
            return False
        row.click_input()          # select row -> in-place editor appears
        time.sleep(0.25)

        ctrls = self._grid_controls()   # re-fetch to get the editor + open arrow
        editor = ctrls["edit"]
        if editor is None:
            self.last_value = ""
            return False
        if ctrls["open"] is not None:   # open the list so the combo has focus
            try:
                ctrls["open"].click_input()
                time.sleep(0.2)
            except Exception:
                pass

        want = value.strip().lower()
        letter = value[0]
        seen = set()
        for _ in range(max_presses):
            send_keys(letter)          # advance to next item starting with letter
            time.sleep(0.1)
            cur = _value(editor).strip()
            self.last_value = cur
            if cur.lower() == want:
                return True
            if cur.lower() in seen:    # cycled all the way around -> not present
                break
            seen.add(cur.lower())
        return False

    def read_editor_value(self, row_name: str) -> str:
        """Read back a row's current committed value (selects the row first)."""
        import time
        row = self._grid_controls()["buttons"].get(row_name)
        if row is None:
            return ""
        row.click_input()
        time.sleep(0.2)
        edit = self._grid_controls()["edit"]
        return _value(edit) if edit is not None else ""

    # -- Design: ribbon -----------------------------------------------------

    def click_ribbon(self, name: str) -> bool:
        """Click a ribbon button by name (e.g. 'Save')."""
        try:
            self.design().child_window(title=name, control_type="Button").click_input()
            return True
        except Exception:
            return False


def _selftest() -> int:
    try:
        boost = BoostUIA()
    except ImportError:
        print("pywinauto not installed. Run: pip install --user pywinauto")
        return 2

    print(f"HomeZone open : {boost.has_home()}")
    print(f"Design open   : {boost.has_design()}")

    if boost.has_home():
        parts = boost.parts()
        print(f"\nParts in list ({len(parts)}):")
        for p in parts:
            print(f"  - {p['name']!r:16}  raw={p['raw']!r}")

    if boost.has_design():
        print(f"\nDimensions read: {boost.read_dimensions()!r}")
        rows = boost.property_rows()
        print(f"Property grid rows ({len(rows)}):")
        for r in rows:
            print(f"  - {r!r}")
        print("\n('More...' present:", "More..." in rows, ")")
    else:
        print("\n(Open a part in Design view, select the part-number text, and "
              "show the Properties tab to test dimensions + property grid.)")
    return 0


def _do_font_test(value: str) -> int:
    """Observable font test. Mutates the open part -- do NOT save after."""
    import time
    try:
        boost = BoostUIA()
    except ImportError:
        print("pywinauto not installed. Run: pip install --user pywinauto")
        return 2
    if not boost.has_design():
        print("Open a part in Design view with the part-number text selected first.")
        return 1

    print("NOTE: this changes the open part. Undo (Ctrl+Z) / don't save to revert.\n")
    print(f"Setting Font type -> {value!r} (letter-cycle) ...")
    t0 = time.time()
    ok = boost.set_font_type(value)
    dt = time.time() - t0
    print(f"  set_font_type -> {ok}   (last value seen: {boost.last_value!r}, {dt:.1f}s)")
    print("Compare against what Boost shows on screen and tell me if it took.")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoBoost UIA driver.")
    parser.add_argument("--selftest", action="store_true",
                        help="Read-only connectivity + control check (default).")
    parser.add_argument("--set-font", metavar="VALUE", default=None,
                        help="Set the 'Font type' row to VALUE by letter-cycle "
                             "(e.g. 'EasyType-L=10mm'). Mutates the open part.")
    args = parser.parse_args()

    if args.set_font is not None:
        return _do_font_test(value=args.set_font)
    return _selftest()


if __name__ == "__main__":
    sys.exit(main())
