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
    """Best-effort read of an Edit/value control across pywinauto versions."""
    for attempt in (
        lambda: wrapper.get_value(),
        lambda: wrapper.iface_value.CurrentValue,
        lambda: wrapper.legacy_properties().get("Value"),
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
        self.desktop = Desktop(backend="uia")

    # -- window handles -----------------------------------------------------

    def home(self):
        return self.desktop.window(title=HOME_TITLE, control_type="Window")

    def design(self):
        return self.desktop.window(title_re=DESIGN_TITLE_RE, control_type="Window")

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
        return self.design().child_window(auto_id="propertyGrid1")

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

    # When a value row is selected, an in-place editor appears with these ids.
    _EDITOR_AUTOID = "9765996"     # the WinForms EDIT holding the value
    _OPEN_AUTOID = "4261530"       # its dropdown 'Open' arrow

    def _select_row_editor(self, row_name: str, timeout: float = 2.0):
        """Select a property row and return its in-place EDIT wrapper (or None).

        The value dropdown is owner-drawn (invisible to UIA), so we do not touch
        the list -- we type into this editor instead.
        """
        import time
        try:
            self._property_grid().child_window(
                title=row_name, control_type="Button").click_input()
        except Exception:
            return None
        edit = self._property_grid().child_window(auto_id=self._EDITOR_AUTOID)
        try:
            edit.wait("exists ready", timeout=timeout)
            return edit.wrapper_object()
        except Exception:
            return None

    def type_value(self, row_name: str, value: str) -> bool:
        """Select `row_name` and set its value to `value` via keyboard.

        Used for both adding a user-defined property ('More...' -> 'Font type')
        and setting a value ('Font type' -> 'EasyType-L=10MM'), since both use
        the same owner-drawn dropdown that only responds to typing/selection.
        """
        from pywinauto.keyboard import send_keys
        editor = self._select_row_editor(row_name)
        if editor is None:
            return False
        try:
            editor.set_focus()
        except Exception:
            pass
        # Clear anything present, type the exact value, commit with Enter.
        # value is escaped so characters like -, =, digits are sent literally.
        try:
            send_keys("^a{DEL}")
            send_keys(value, with_spaces=True, pause=0.02)
            send_keys("{ENTER}")
            return True
        except Exception:
            # Fallback: ValuePattern SetValue (may not always commit).
            try:
                editor.set_edit_text(value)
                send_keys("{ENTER}")
                return True
            except Exception:
                return False

    def add_font_type(self) -> bool:
        """Add the 'Font type' user-defined property via the 'More...' row."""
        return self.type_value("More...", "Font type")

    def set_font_type(self, value: str = "EasyType-L=10MM") -> bool:
        """Set the 'Font type' value (defaults to Iso) to `value`."""
        return self.type_value("Font type", value)

    def read_editor_value(self, row_name: str) -> str:
        """Select a row and read back its in-place editor value (for verifying)."""
        editor = self._select_row_editor(row_name)
        return _value(editor) if editor is not None else ""

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


def _do_font_test(add: bool, value: str) -> int:
    """Observable font-chain test. Mutates the open part -- do NOT save after."""
    try:
        boost = BoostUIA()
    except ImportError:
        print("pywinauto not installed. Run: pip install --user pywinauto")
        return 2
    if not boost.has_design():
        print("Open a part in Design view with the part-number text selected first.")
        return 1

    print("NOTE: this changes the open part. Undo (Ctrl+Z) / don't save to revert.\n")
    if add:
        print("Adding 'Font type' property via 'More...' ...")
        print(f"  add_font_type -> {boost.add_font_type()}")
        import time
        time.sleep(1.0)
    print(f"Setting Font type -> {value!r} ...")
    ok = boost.set_font_type(value)
    print(f"  set_font_type -> {ok}")
    import time
    time.sleep(0.8)
    print(f"\nRead back Font type value: {boost.read_editor_value('Font type')!r}")
    print("Compare against what Boost shows on screen and tell me if it took.")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoBoost UIA driver.")
    parser.add_argument("--selftest", action="store_true",
                        help="Read-only connectivity + control check (default).")
    parser.add_argument("--set-font", metavar="VALUE", default=None,
                        help="Set the existing 'Font type' row to VALUE "
                             "(e.g. 'EasyType-L=10MM'). Mutates the open part.")
    parser.add_argument("--add-and-set-font", metavar="VALUE", default=None,
                        help="Add the 'Font type' property, then set it to VALUE. "
                             "Mutates the open part.")
    args = parser.parse_args()

    if args.set_font is not None:
        return _do_font_test(add=False, value=args.set_font)
    if args.add_and_set_font is not None:
        return _do_font_test(add=True, value=args.add_and_set_font)
    return _selftest()


if __name__ == "__main__":
    sys.exit(main())
