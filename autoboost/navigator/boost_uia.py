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
CUT_TITLE_RE = r".* - TruTops Boost - Cut"


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
        Timings.window_find_timeout = 1
        self.desktop = Desktop(backend="uia")
        self._home = None
        self._design = None
        self._cut = None
        self._grid = None
        self._table = None
        self._options = None
        self.last_value = ""   # last value observed by a set operation (for tests)

    # -- window handles -----------------------------------------------------
    # Window/grid specs are cached: resolving them re-searches the UIA tree,
    # which is the main source of slowness. Call reset() if windows change.

    def reset(self) -> None:
        self._home = self._design = self._cut = None
        self._grid = self._table = self._options = None

    def home(self):
        if self._home is None:
            self._home = self.desktop.window(title=HOME_TITLE, control_type="Window")
        return self._home

    def design(self):
        if self._design is None:
            self._design = self.desktop.window(title_re=DESIGN_TITLE_RE, control_type="Window")
        return self._design

    def cut(self):
        if self._cut is None:
            self._cut = self.desktop.window(title_re=CUT_TITLE_RE, control_type="Window")
        return self._cut

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

    def has_cut(self) -> bool:
        try:
            return self.cut().exists(timeout=1)
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

    def open_part_in_design(self, name: str | None = None, timeout: int = 25) -> bool:
        """Open a part into Design view: optionally select `name` in the list,
        then click the Design section's 'Open' button and wait for the Design
        window. Records failure detail in self.last_value."""
        import time
        home = self.home()
        if name is not None:
            if not self.select_part(name):
                self.last_value = f"<part {name!r} not in list>"
                return False
            time.sleep(0.6)

        def click_open() -> bool:
            try:
                b = home.child_window(auto_id="Part.Detail.Design.Open",
                                      control_type="Button")
                if b.exists(timeout=1):
                    b.click_input()
                    return True
            except Exception:
                pass
            return False

        if not click_open():
            # The Design detail section may be collapsed -- expand it and retry.
            try:
                home.child_window(auto_id="Part.Detail.Design").click_input()
                time.sleep(0.6)
            except Exception:
                pass
            if not click_open():
                self.last_value = "<Design 'Open' button not found>"
                return False

        self.reset()                      # a new Design window is opening
        for _ in range(timeout):
            if self.has_design():
                return True
            time.sleep(1)
        self.last_value = "<Design view did not open>"
        return False

    # -- HomeZone: cutting programs -----------------------------------------
    #
    # Same window and naming convention as the Design 'Open' button
    # (Part.Detail.Design.Open). We drive the cutting-program flow that hangs
    # off every part's Home detail page:
    #     Cutting Programs 'New'  ->  set 'Allowed angular positions (Job)'
    #     ->  the program row's 'Open'  ->  the Cut window opens.
    #
    # The exact auto_ids are pinned below once locate_cut_controls() reports
    # them; until then each lookup falls back to positional anchoring on the
    # 'Cutting Programs' header / the 'Allowed angular positions (Job)' label,
    # the same label-then-neighbour technique read_dimensions() uses.

    # Confirmed from locate_cut_controls(): the section is 'CutSolutions'
    # (header auto_id Part.Detail.CutSolutions.Header.Text) and its 'New' is
    # Part.Detail.CutSolutions.AddSolution -- distinct from the Bending row's
    # Part.Detail.BendSolutions.AddSolution.
    CUT_NEW_AUTOIDS = ("Part.Detail.CutSolutions.AddSolution",)
    # Pinned from a post-New locate: a freshly created program is 'Cut1', so its
    # row 'Open' and its '(Job)' combo carry these ids. The positional/label
    # fallbacks still cover a differently-named program (Cut2, ...).
    CUT_OPEN_AUTOIDS = ("Part.Detail.CutSolutions.List.Cut1.OpenSolution",
                        "Part.Detail.CutSolutions.Open")
    ANGULAR_JOB_AUTOID = "Part.Detail.CutSolutions.List.Cut1.Detail.PermittedNestingOrientations"
    ANGULAR_JOB_LABEL = "Allowed angular positions (Job)"

    def _home_wrapper(self):
        return self.home().wrapper_object()

    @staticmethod
    def _cy(rect) -> int:
        return (rect.top + rect.bottom) // 2

    def _named_buttons(self, name: str) -> list:
        """Every HomeZone Button whose visible text equals `name`."""
        return [b for b in self._home_wrapper().descendants(control_type="Button")
                if _text(b) == name]

    def _cut_header(self):
        """The 'Cutting Programs (N)' section header Text wrapper, or None."""
        for t in self._home_wrapper().descendants(control_type="Text"):
            if _text(t).startswith("Cutting Programs"):
                return t
        return None

    def _find_home_by_autoids(self, autoids, control_type="Button"):
        home = self.home()
        for aid in autoids:
            try:
                b = home.child_window(auto_id=aid, control_type=control_type)
                if b.exists(timeout=0):
                    return b.wrapper_object()
            except Exception:
                continue
        return None

    def find_cut_new_button(self):
        """Locate the 'New' button on the Cutting Programs row.

        Returns (wrapper, how) where how is the strategy that found it, or
        (None, reason). There is also a 'New' on the Bending Programs row, so a
        bare name match is not enough -- we require it to sit on the Cutting
        Programs header row (or match the pinned auto_id).
        """
        b = self._find_home_by_autoids(self.CUT_NEW_AUTOIDS)
        if b is not None:
            return b, "auto_id"
        hdr = self._cut_header()
        if hdr is None:
            return None, "'Cutting Programs' header not found"
        hr = hdr.rectangle()
        cy = self._cy(hr)
        best = None
        for btn in self._named_buttons("New"):
            r = btn.rectangle()
            if abs(self._cy(r) - cy) < 20 and r.left > hr.left:
                if best is None or r.left < best.rectangle().left:
                    best = btn
        return (best, "positional") if best else (None, "no 'New' on the Cutting Programs row")

    def _angular_combo(self):
        """The '(Job)' angular-positions ComboBox (distinct from the Design
        section's 'Allowed angular positions')."""
        # Prefer the pinned auto_id (fast, unambiguous).
        try:
            c = self.home().child_window(auto_id=self.ANGULAR_JOB_AUTOID,
                                         control_type="ComboBox")
            if c.exists(timeout=1):
                return c.wrapper_object()
        except Exception:
            pass
        # Fallback: the ComboBox directly under the '(Job)' label.
        home = self._home_wrapper()
        labels = [t for t in home.descendants(control_type="Text")
                  if _text(t) == self.ANGULAR_JOB_LABEL]
        if not labels:
            return None
        lr = labels[0].rectangle()
        best = None
        for c in home.descendants(control_type="ComboBox"):
            r = c.rectangle()
            below = 0 <= r.top - lr.bottom < 45
            aligned = abs(r.left - lr.left) < 80
            if below and aligned and (best is None or r.top < best.rectangle().top):
                best = c
        return best

    def set_cut_angular_positions(self, value: str = "0°;90°") -> bool:
        """Set the 'Allowed angular positions (Job)' combo to `value`.

        This HomeZone combo is a real WPF ComboBox (unlike the owner-drawn
        Design font list), so the UIA select works; a click-open + pick fallback
        covers a non-standard skin. Records the outcome in self.last_value.
        """
        import time
        combo = self._angular_combo()
        if combo is None:
            self.last_value = "<angular-positions (Job) combo not found>"
            return False
        try:
            combo.select(value)
            self.last_value = value
            return True
        except Exception:
            pass
        # Fallback: open the list and click the matching item.
        try:
            combo.click_input()
            time.sleep(0.4)
            for item in self._home_wrapper().descendants(control_type="ListItem"):
                if _text(item).strip() == value:
                    item.click_input()
                    self.last_value = value
                    return True
        except Exception:
            pass
        self.last_value = f"<could not set angular positions to {value!r}>"
        return False

    def set_cut_angular_last(self) -> bool:
        """Select the LAST option ('0°;90°...') in the '(Job)' combo.

        Uses the SAME native combo.select() that reliably set '0°;90°' -- the
        only thing that ever needed to change was the target string, not the
        method (the item_texts/keyboard detour was the regression). Selects the
        dotted last value by string (trying a plain '...' and an ellipsis glyph),
        then by index as a fallback, and confirms via read-back so a miss returns
        False. Records the committed value in self.last_value.
        """
        import time
        combo = self._angular_combo()
        if combo is None:
            self.last_value = "<angular-positions (Job) combo not found>"
            return False

        def current() -> str:
            return (_value(combo) or "").strip()

        before = current()

        # 1. Select the dotted last value by string (the proven select() path).
        for cand in ("0°;90°...", "0°;90°…"):
            try:
                combo.select(cand)
                time.sleep(0.4)
                after = current()
                if after and after != before:
                    self.last_value = after
                    return True
            except Exception:
                continue

        # 2. Fallback: select the last item by index (uia combo has item_count()).
        try:
            n = combo.item_count()
        except Exception:
            n = 0
        if n:
            try:
                combo.select(n - 1)
                time.sleep(0.4)
                after = current()
                if after and after != before:
                    self.last_value = after
                    return True
            except Exception:
                pass

        self.last_value = f"<angular unchanged: still {current()!r}>"
        return False

    def find_cut_open_button(self):
        """The 'Open' button on the newly-created cutting-program row.

        Returns (wrapper, how) or (None, reason). Prefers the pinned auto_id;
        otherwise the top-most 'Open' below the Cutting Programs header (the
        program row sits directly under it; the Design 'Open' is above)."""
        b = self._find_home_by_autoids(self.CUT_OPEN_AUTOIDS)
        if b is not None:
            return b, "auto_id"
        hdr = self._cut_header()
        if hdr is None:
            return None, "'Cutting Programs' header not found"
        hb = hdr.rectangle().bottom
        below = [(btn.rectangle().top, btn) for btn in self._named_buttons("Open")
                 if self._cy(btn.rectangle()) > hb]
        if not below:
            return None, "no 'Open' below the Cutting Programs header"
        below.sort(key=lambda x: x[0])
        return below[0][1], "positional"

    def open_cut_program(self, timeout: int = 25) -> bool:
        """Click the cutting-program row's 'Open' and wait for the Cut window."""
        import time
        btn, how = self.find_cut_open_button()
        if btn is None:
            self.last_value = f"<cut 'Open' button not found: {how}>"
            return False
        btn.click_input()
        self.reset()                      # a new Cut window is opening
        for _ in range(timeout):
            if self.has_cut():
                return True
            time.sleep(1)
        self.last_value = "<Cut window did not open>"
        return False

    def create_cut_program(self, angular: str | None = None, log=print) -> bool:
        """New cutting program -> set angular positions -> open the Cut window.

        angular=None (default) selects the LAST option in the combo ('0°;90°...')
        by keyboard, which is the wanted value and dodges the awkward string.
        Pass an explicit value (e.g. '0°;90°') to select it by name instead.

        Assumes the target part is already selected in the Home list. Each step
        logs so a failure points at one action.
        """
        import time
        btn, how = self.find_cut_new_button()
        if btn is None:
            self.last_value = f"<cut 'New' not found: {how}>"
            log(f"cut 'New' -> not found ({how})")
            return False
        btn.click_input()
        log(f"clicked Cutting Programs 'New' ({how})")
        time.sleep(1.0)                   # the new Cut1 row + settings appear

        if angular is None:
            ok = self.set_cut_angular_last()
            wanted = "(last option)"
        else:
            ok = self.set_cut_angular_positions(angular)
            wanted = angular
        if not ok:
            log(f"set angular positions -> False ({self.last_value})")
            return False
        log(f"set '{self.ANGULAR_JOB_LABEL}' -> {self.last_value} {wanted}")
        time.sleep(0.4)

        if not self.open_cut_program():
            log(f"open cut program -> False ({self.last_value})")
            return False
        log("cut program opened (Cut window)")
        return True

    def click_cut_apply_technology(self, dry_run: bool = False, offset=None) -> bool:
        """Click the 'All : <machine>' auto-apply cutting-technology button on
        the Cut window's Start ribbon tab.

        The Qtitan ribbon exposes no buttons to UIA, so this is a positional
        click within the maximized Cut window (offset from its top-left, in
        config.cut.apply_button_offset). dry_run just moves the cursor there so
        the target can be eyeballed without clicking. Records the point in
        self.last_value.

        TODO (hardening): make this fool-proof against a non-maximized / oddly
        placed Cut window -- e.g. maximize it first, or scale the offset by the
        window size -- so the positional click can't miss.
        """
        import time
        import pyautogui
        from ..config import DEFAULT
        if not self.has_cut():
            self.last_value = "<Cut window not open>"
            return False
        win = self.cut().wrapper_object()
        try:
            win.set_focus()
        except Exception:
            pass
        time.sleep(0.3)
        r = win.rectangle()
        ox, oy = offset or DEFAULT.cut.apply_button_offset
        x, y = r.left + int(ox), max(0, r.top) + int(oy)
        if dry_run:
            pyautogui.moveTo(x, y)
            self.last_value = f"<dry-run: cursor at ({x},{y}); no click>"
            return True
        pyautogui.click(x, y)
        self.last_value = f"clicked ({x},{y})"
        return True

    def finish_cut_program(self, log=print) -> bool:
        """Complete an open Cut window: auto-apply the cutting technology, wait
        for it to compute, dismiss the completion notice (Esc), save (Ctrl+S),
        and close the window (Alt+F4) back to Home.

        Assumes a Cut window is already open (from create_cut_program). Each
        step logs so a failure points at one action.
        """
        import time
        import pyautogui
        from ..config import DEFAULT
        cut = DEFAULT.cut
        if not self.has_cut():
            self.last_value = "<Cut window not open>"
            log("no Cut window open")
            return False

        # 1. Auto-apply cutting technology (focuses the Cut window + clicks).
        if not self.click_cut_apply_technology():
            log(f"apply technology -> False ({self.last_value})")
            return False
        log("clicked auto-apply cutting technology")

        # 2. Wait for the data to compute; the completion notice then appears.
        time.sleep(cut.apply_wait_s)

        # 3. Dismiss the completion notice.
        pyautogui.press("esc")
        time.sleep(0.4)
        log("dismissed completion notice (esc)")

        # 4. Save the cutting program.
        pyautogui.hotkey("ctrl", "s")
        time.sleep(cut.after_save_s)
        log("saved (ctrl+s)")

        # 5. Close the Cut window (Alt+F4) -> back to Home. We saved first, so
        #    no unsaved-changes prompt should appear.
        pyautogui.hotkey("alt", "f4")
        self.reset()                      # the Cut window is going away
        for _ in range(cut.close_timeout_s):
            if self.has_home() and not self.has_cut():
                log("closed Cut window (alt+f4) -> Home")
                return True
            time.sleep(1)
        self.last_value = "<Cut window did not close back to Home>"
        log(self.last_value)
        return False

    def locate_cut_controls(self) -> str:
        """Read-only report of the cutting-program controls and their auto_ids.

        Clicks nothing. Run it with the part selected (and again after a manual
        'New') to capture the exact auto_ids to pin in CUT_*_AUTOIDS."""
        home = self._home_wrapper()
        lines = [f"HomeZone open: {self.has_home()}"]
        hdr = self._cut_header()
        if hdr is not None:
            r = hdr.rectangle()
            lines.append(f"header {_text(hdr)!r} auto_id={_auto_id(hdr)!r} "
                         f"rect=({r.left},{r.top},{r.right},{r.bottom})")
        else:
            lines.append("header 'Cutting Programs': NOT FOUND")
        for name in ("New", "Open"):
            for b in self._named_buttons(name):
                r = b.rectangle()
                lines.append(f"  {name:4} auto_id={_auto_id(b)!r} "
                             f"rect=({r.left},{r.top},{r.right},{r.bottom})")
        for t in home.descendants(control_type="Text"):
            if "angular positions" in _text(t).lower():
                r = t.rectangle()
                lines.append(f"  label {_text(t)!r} auto_id={_auto_id(t)!r} "
                             f"rect=({r.left},{r.top},{r.right},{r.bottom})")
        for c in home.descendants(control_type="ComboBox"):
            r = c.rectangle()
            lines.append(f"  ComboBox auto_id={_auto_id(c)!r} value={_value(c)!r} "
                         f"rect=({r.left},{r.top},{r.right},{r.bottom})")
            if "angular" in _auto_id(c).lower() or "orientation" in _auto_id(c).lower():
                for label, getter in (("texts", c.texts),
                                      ("item_count", c.item_count)):
                    try:
                        lines.append(f"       {label}={getter()!r}")
                    except Exception as exc:
                        lines.append(f"       {label}=<unreadable: {exc!r}>")
        return "\n".join(lines)

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

    def _grid_table(self):
        """Cached wrapper for the property grid's Table (rows/editor/arrow are
        its DIRECT children, so a shallow children() scan is fast)."""
        if self._table is None:
            self._table = self._property_grid().child_window(
                control_type="Table").wrapper_object()
        return self._table

    def _options_panel(self):
        """Cached wrapper for the Design 'Options' side panel. The add-property
        selector box lives here (a sibling of the grid), so it is not visible in
        the grid Table's children -- scan this panel's descendants for it."""
        if self._options is None:
            self._options = self.design().child_window(
                auto_id="toolOptionsSideBar").wrapper_object()
        return self._options

    def _grid_controls(self) -> dict:
        """Shallow children() scan -> {'buttons': {name: wrapper}, 'edit',
        'open', 'opens': [all Open arrows]}.

        Identify the in-place editor by control type (Edit) and the dropdown
        arrow by its name ('Open'). Numeric auto-ids from the probe dumps are
        DotNetBar runtime ids that change between sessions -- never depend on
        them. Uses the Table's direct children (fast) instead of descendants().
        """
        def ctype(c):
            try:
                return c.element_info.control_type
            except Exception:
                return ""

        buttons, edit, opens = {}, None, []
        for c in self._grid_table().children():
            ct = ctype(c)
            if ct == "Edit" and edit is None:
                edit = c
            elif ct == "Button":
                name = _text(c)
                if name == "Open":
                    opens.append(c)
                elif name:
                    buttons.setdefault(name, c)
        return {"buttons": buttons, "edit": edit,
                "open": (opens[0] if opens else None), "opens": opens}

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

    def set_font_type_by_image(self, template_path: str,
                               target: str = "EasyType-L=10mm",
                               confidence: float = 0.85) -> bool:
        """Pick the font by clicking its row in the open (owner-drawn) dropdown.

        The value list renders as pixels with no UIA and the combo commits on the
        first letter, so keyboard can't reach a specific item -- we click it like
        a human. Opens the dropdown via UIA, template-matches `template_path`
        (a tight crop of the target row) on screen, clicks it, and verifies with
        the read-back oracle. Records the outcome in self.last_value.
        """
        import time
        import pyautogui

        ctrls = self._grid_controls()
        row = ctrls["buttons"].get("Font type")
        if row is None:
            self.last_value = ""
            return False
        row.click_input()
        time.sleep(0.25)
        ctrls = self._grid_controls()
        if ctrls["open"] is not None:
            try:
                ctrls["open"].click_input()
                time.sleep(0.35)
            except Exception:
                pass

        # Restrict the search to the Design window so stray on-screen text can't
        # win, then click the matched row's centre.
        region = None
        try:
            r = self.design().rectangle()
            region = (r.left, r.top, r.width(), r.height())
        except Exception:
            pass
        try:
            loc = pyautogui.locateCenterOnScreen(
                template_path, confidence=confidence, region=region)
        except Exception:
            loc = None
        if loc is None:
            from pywinauto.keyboard import send_keys
            send_keys("{ESC}")
            self.last_value = ""
            return False
        pyautogui.click(loc)
        time.sleep(0.3)

        val = self.read_editor_value("Font type")
        self.last_value = val
        return val.strip().lower() == target.strip().lower()

    # Font dropdown items, top-to-bottom (from the on-screen list). Update this
    # if the shop's font table changes.
    FONT_OPTIONS = [
        "Iso", "Iso Prop", "Bold",
        "EasyType-L=4mm", "EasyType-L=5mm", "EasyType-L=6mm", "EasyType-L=8mm",
        "EasyType-L=8.5mm", "EasyType-L=10mm", "Digital Font-H=3mm",
    ]

    def _open_font_dropdown(self):
        """Select the Font type row and click its dropdown arrow. Returns the
        editor wrapper (for read-back) or None."""
        import time
        ctrls = self._grid_controls()
        row = ctrls["buttons"].get("Font type")
        if row is None:
            return None
        row.click_input()
        time.sleep(0.25)
        ctrls = self._grid_controls()
        if ctrls["open"] is not None:
            try:
                ctrls["open"].click_input()
            except Exception:
                pass
        time.sleep(0.35)
        return ctrls["edit"]

    def set_font_type_by_position(self, target: str = "EasyType-L=10mm",
                                  options: list[str] | None = None,
                                  dry_run: bool = False,
                                  out_path: str = "font_dropdown.png") -> bool:
        """Pick the font by clicking its row in the open owner-drawn list.

        No template needed: screenshot before/after opening the dropdown, diff to
        find the list box, then click the target's row by its index in `options`.
        dry_run marks the intended click on a saved overlay and clicks nothing.
        """
        import time
        import numpy as np
        import cv2
        import pyautogui
        from pywinauto.keyboard import send_keys

        options = options or self.FONT_OPTIONS
        if target not in options:
            self.last_value = f"<target {target!r} not in options>"
            return False
        idx = options.index(target)

        before = cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2GRAY)
        editor = self._open_font_dropdown()
        after_rgb = np.array(pyautogui.screenshot())
        after = cv2.cvtColor(after_rgb, cv2.COLOR_RGB2GRAY)

        # The dropdown is the largest region that changed between the frames.
        diff = cv2.absdiff(before, after)
        _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            send_keys("{ESC}")
            self.last_value = "<dropdown not detected>"
            return False
        x, y, w, h = cv2.boundingRect(max(contours, key=cv2.contourArea))

        row_h = h / len(options)
        cx = x + w // 2
        cy = int(y + (idx + 0.5) * row_h)

        if dry_run:
            # Save the raw after-frame too, so if the dropdown wasn't open we can
            # see that rather than guessing.
            cv2.imwrite("font_dropdown_raw.png", cv2.cvtColor(after_rgb, cv2.COLOR_RGB2BGR))
            dbg = cv2.cvtColor(after_rgb, cv2.COLOR_RGB2BGR)
            cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 200, 0), 2)
            for i in range(len(options) + 1):     # row separators
                yy = int(y + i * row_h)
                cv2.line(dbg, (x, yy), (x + w, yy), (0, 150, 0), 1)
            cv2.circle(dbg, (cx, cy), 6, (0, 0, 255), -1)
            cv2.imwrite(out_path, dbg)
            send_keys("{ESC}")
            self.last_value = f"<dry-run: would click ({cx},{cy}) row {idx} -> {out_path}>"
            return True

        pyautogui.click(cx, cy)
        time.sleep(0.3)
        val = self.read_editor_value("Font type")
        self.last_value = val
        return val.strip().lower() == target.strip().lower()

    def _read_font_value(self) -> str:
        edit = self._grid_controls()["edit"]
        return _value(edit).strip() if edit is not None else ""

    def _font_arrow_center(self):
        """Select the Font type row and return the (x,y) centre of its dropdown
        'Open' arrow in screen coords (or (None,None))."""
        import time
        row = self._grid_controls()["buttons"].get("Font type")
        if row is None:
            return None, None
        row.click_input()
        time.sleep(0.25)
        ob = self._grid_controls()["open"]
        if ob is None:
            return None, None
        r = ob.rectangle()
        return (r.left + r.right) // 2, (r.top + r.bottom) // 2

    def set_font_by_drag(self, target: str = "EasyType-L=10mm",
                         options: list[str] | None = None,
                         dry_run: bool = False,
                         out_path: str = "font_dropdown.png",
                         retries: int = 3) -> bool:
        """Select the font by clicking its row in the open dropdown.

        Recipe (from observed behavior): click the arrow -> the list opens and
        STAYS open -> single-click the target option to commit. We open with a
        real OS click, screenshot the (now open) list, diff to find its box, and
        click the target row. The read-back oracle verifies; on a miss we learn
        the real row height from which row we hit and click again. dry_run marks
        the target row on an overlay and closes with Esc (no commit).
        """
        import time
        import numpy as np
        import cv2
        import pyautogui
        from pywinauto.keyboard import send_keys

        options = options or self.FONT_OPTIONS
        if target not in options:
            self.last_value = f"<target {target!r} not in options>"
            return False
        idx = options.index(target)

        ax, ay = self._font_arrow_center()
        if ax is None:
            self.last_value = "<Font type row / open arrow not found>"
            return False

        def gray_shot():
            return cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2GRAY)

        box = None       # (x,y,w,h) of the list, detected once
        row_h = None     # refined from read-back misses
        for attempt in range(retries + 1):
            before = gray_shot()
            pyautogui.click(ax, ay)              # open the list (stays open)
            time.sleep(0.4)
            after_rgb = np.array(pyautogui.screenshot())
            after = cv2.cvtColor(after_rgb, cv2.COLOR_RGB2GRAY)

            if box is None:
                diff = cv2.absdiff(before, after)
                _, th = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
                th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
                contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                if not contours:
                    send_keys("{ESC}")
                    self.last_value = "<dropdown not detected>"
                    return False
                box = cv2.boundingRect(max(contours, key=cv2.contourArea))
                row_h = box[3] / len(options)

            x, y, w, h = box
            tx = x + w // 2
            ty = int(y + (idx + 0.5) * row_h)

            if dry_run:
                cv2.imwrite("font_dropdown_raw.png",
                            cv2.cvtColor(after_rgb, cv2.COLOR_RGB2BGR))
                dbg = cv2.cvtColor(after_rgb, cv2.COLOR_RGB2BGR)
                cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 200, 0), 2)
                for i in range(len(options) + 1):
                    yy = int(y + i * row_h)
                    cv2.line(dbg, (x, yy), (x + w, yy), (0, 150, 0), 1)
                cv2.circle(dbg, (tx, ty), 6, (0, 0, 255), -1)
                cv2.imwrite(out_path, dbg)
                send_keys("{ESC}")               # close without committing
                self.last_value = f"<dry-run: box={box} target row {idx} at ({tx},{ty})>"
                return True

            pyautogui.click(tx, ty)              # click the option -> commit
            time.sleep(0.3)
            val = self.read_editor_value("Font type")
            self.last_value = val
            if val.strip().lower() == target.strip().lower():
                return True
            # Missed: use which row we actually hit to solve the true row height,
            # then aim again next loop (re-opens the list).
            if val in options:
                hit = options.index(val)
                if hit != idx:
                    row_h = (ty - y) / (hit + 0.5)
        return False

    def set_font_by_cycle_click(self, target: str = "EasyType-L=10mm",
                                max_clicks: int = 16) -> bool:
        """Advance the Font type value by double-clicking the row, reading back
        after each click, stopping when it equals `target`.

        Relies on the common PropertyGrid behavior where double-clicking an
        enum/combo row steps to the next value. No dropdown or vision needed.
        Records the last value seen in self.last_value.
        """
        import time
        row = self._grid_controls()["buttons"].get("Font type")
        if row is None:
            self.last_value = ""
            return False
        row.click_input()          # select the row
        time.sleep(0.2)
        want = target.strip().lower()
        seen = set()
        for _ in range(max_clicks):
            cur = self._read_font_value()
            self.last_value = cur
            if cur.lower() == want:
                return True
            if cur.lower() in seen:   # value repeated -> not cycling / wrapped
                break
            seen.add(cur.lower())
            try:
                row.double_click_input()
            except Exception:
                break
            time.sleep(0.15)
        return self.last_value.strip().lower() == want

    def add_font_type(self) -> bool:
        """Add the 'Font type' user-defined property to the selected text.

        Recipe (from observed behavior): click 'More...' -> click the arrow that
        appears on that row -> a selector box opens in the Options panel with a
        ComboBox + Add/Delete buttons. Click the ComboBox, press F ('Font type'
        is the only F option, so it selects and commits), then click Add. Every
        click stays inside the box, which is required -- it closes on any click
        outside it. Records the outcome in self.last_value.
        """
        import time
        from pywinauto.keyboard import send_keys

        c = self._grid_controls()
        more = c["buttons"].get("More...")
        if more is None:
            self.last_value = "<More... row not found>"
            return False
        more.click_input()
        time.sleep(0.3)

        c = self._grid_controls()
        if not c["opens"]:
            self.last_value = "<More... dropdown arrow not found>"
            return False
        c["opens"][0].click_input()       # opens the property selector box
        time.sleep(0.5)

        # The selector box (ComboBox + Add/Delete) is owner-drawn, but it takes
        # keyboard: Tab into the property combo, F selects 'Font type' (the only
        # F option), Tab to the Add button, Enter to add it. All keys, no clicks
        # outside the box (which would close it).
        for key in ("{TAB}", "f", "{TAB}", "{ENTER}"):
            send_keys(key)
            time.sleep(0.15)
        time.sleep(0.4)

        present = "Font type" in self._grid_controls()["buttons"]
        self.last_value = "Font type row present" if present else "<add may have failed>"
        return present

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


def _do_font_image_test(template: str, target: str) -> int:
    """Observable vision-click font test. Mutates the open part -- don't save."""
    import time
    try:
        boost = BoostUIA()
    except ImportError:
        print("pywinauto not installed. Run: pip install --user pywinauto")
        return 2
    if not boost.has_design():
        print("Open a part in Design view with the Font type row present first.")
        return 1
    print("NOTE: this changes the open part. Undo (Ctrl+Z) / don't save to revert.\n")
    print(f"Picking Font type -> {target!r} by clicking template {template!r} ...")
    t0 = time.time()
    ok = boost.set_font_type_by_image(template, target)
    dt = time.time() - t0
    print(f"  set_font_type_by_image -> {ok}   (read back: {boost.last_value!r}, {dt:.1f}s)")
    if not ok and not boost.last_value:
        print("  (template not found on screen -- recrop tighter, or the dropdown "
              "didn't open. Tell me and send a screenshot of the open list.)")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="AutoBoost UIA driver.")
    parser.add_argument("--selftest", action="store_true",
                        help="Read-only connectivity + control check (default).")
    parser.add_argument("--set-font", metavar="VALUE", default=None,
                        help="Set the 'Font type' row to VALUE by letter-cycle "
                             "(e.g. 'EasyType-L=10mm'). Mutates the open part.")
    parser.add_argument("--set-font-image", metavar="TEMPLATE", default=None,
                        help="Pick Font type by clicking the row matching this "
                             "template image in the open dropdown.")
    parser.add_argument("--target", default="EasyType-L=10mm",
                        help="Expected value for the vision font tests.")
    parser.add_argument("--set-font-pos", action="store_true",
                        help="Pick the font by clicking its row position in the "
                             "open dropdown (no template needed).")
    parser.add_argument("--dry-run", action="store_true",
                        help="With --set-font-pos: mark the intended click on a "
                             "saved overlay and click nothing.")
    parser.add_argument("--set-font-dblclick", action="store_true",
                        help="Set Font type by double-clicking the row to advance "
                             "it to --target (no dropdown/vision).")
    parser.add_argument("--set-font-drag", action="store_true",
                        help="Set Font type via held mouse-drag on the open "
                             "dropdown (the only gesture the control honours). "
                             "Add --dry-run to just mark the target row.")
    parser.add_argument("--add-font", action="store_true",
                        help="Add the 'Font type' user-defined property (More... "
                             "-> Font type -> Add). Run on a text with no Font "
                             "type row yet.")
    parser.add_argument("--open-part", metavar="NAME", nargs="?", const="",
                        default=None,
                        help="Open a part into Design view. With NAME, select "
                             "that part in the Home list first; without, open the "
                             "currently selected part.")
    parser.add_argument("--locate-cut", action="store_true",
                        help="Read-only: report the Cutting Programs controls and "
                             "their auto_ids (clicks nothing). Run with the part "
                             "selected, and again after a manual 'New'.")
    parser.add_argument("--new-cut", action="store_true",
                        help="Create a cutting program on the selected part: "
                             "New -> set angular positions -> open the Cut window.")
    parser.add_argument("--angular", default=None,
                        help="Value for 'Allowed angular positions (Job)'. "
                             "Omit to select the LAST option ('0°;90°...') by "
                             "keyboard; pass e.g. '0°;90°' to select by name.")
    parser.add_argument("--cut-apply", action="store_true",
                        help="Click the auto-apply cutting-technology button on "
                             "the open Cut window (Qt ribbon, positional). Add "
                             "--dry-run to only move the cursor there.")
    args = parser.parse_args()

    if args.locate_cut:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_home():
            print("HomeZone window not found. Is Boost on the Home screen?")
            return 1
        print(boost.locate_cut_controls())
        return 0

    if args.new_cut:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_home():
            print("HomeZone window not found. Is Boost on the Home screen?")
            return 1
        import time
        print(f"Creating cutting program (angular={args.angular!r}) ...")
        t0 = time.time()
        ok = boost.create_cut_program(args.angular, log=lambda m: print("  " + m))
        print(f"  result -> {ok}   {boost.last_value!r}   ({time.time()-t0:.1f}s)")
        return 0 if ok else 2

    if args.cut_apply:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_cut():
            print("Cut window not found. Open a cutting program first.")
            return 1
        mode = "DRY-RUN (cursor only)" if args.dry_run else "LIVE (will click)"
        print(f"{mode}: auto-apply cutting technology ...")
        ok = boost.click_cut_apply_technology(dry_run=args.dry_run)
        print(f"  result -> {ok}   {boost.last_value!r}")
        if args.dry_run:
            print("  Is the cursor sitting on the 'All : <machine>' button? If "
                  "it's off, tell me the direction and I'll nudge the offset.")
        return 0 if ok else 2

    if args.open_part is not None:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_home():
            print("HomeZone window not found. Is Boost on the Home screen?")
            return 1
        import time
        name = args.open_part or None
        print(f"Opening part {name or '(currently selected)'} into Design ...")
        t0 = time.time()
        ok = boost.open_part_in_design(name)
        print(f"  result -> {ok}   {boost.last_value!r}   ({time.time()-t0:.1f}s)")
        return 0 if ok else 2

    if args.add_font:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_design():
            print("Open a part in Design view with the part-number text selected first.")
            return 1
        import time
        print("Adding 'Font type' property ...")
        t0 = time.time()
        ok = boost.add_font_type()
        print(f"  result -> {ok}   {boost.last_value!r}   ({time.time()-t0:.1f}s)")
        return 0 if ok else 2

    if args.set_font_drag:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_design():
            print("Open a part in Design view with the Font type row present first.")
            return 1
        import time
        mode = "DRY-RUN (no commit)" if args.dry_run else "LIVE (will commit)"
        print(f"{mode}: selecting {args.target!r} by drag ...")
        t0 = time.time()
        ok = boost.set_font_by_drag(args.target, dry_run=args.dry_run)
        print(f"  result -> {ok}   {boost.last_value!r}   ({time.time()-t0:.1f}s)")
        if args.dry_run:
            print("  Open font_dropdown.png: the red dot should sit on the "
                  "EasyType-L=10mm row. Also font_dropdown_raw.png is the plain "
                  "frame (confirms the list stayed open). Send me font_dropdown.png.")
        return 0 if ok else 2

    if args.set_font_dblclick:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_design():
            print("Open a part in Design view with the Font type row present first.")
            return 1
        import time
        print(f"Double-click-cycling Font type -> {args.target!r} ...")
        t0 = time.time()
        ok = boost.set_font_by_cycle_click(args.target)
        print(f"  result -> {ok}   (last value: {boost.last_value!r}, {time.time()-t0:.1f}s)")
        print("  Tell me what Boost shows and whether the value changed at all.")
        return 0 if ok else 2

    if args.set_font_pos:
        try:
            boost = BoostUIA()
        except ImportError:
            print("pywinauto not installed. Run: pip install --user pywinauto")
            return 2
        if not boost.has_design():
            print("Open a part in Design view with the Font type row present first.")
            return 1
        mode = "DRY-RUN (nothing clicked)" if args.dry_run else "LIVE (will click)"
        print(f"{mode}: picking {args.target!r} by dropdown position ...")
        ok = boost.set_font_type_by_position(args.target, dry_run=args.dry_run)
        print(f"  result -> {ok}   {boost.last_value!r}")
        if args.dry_run:
            print("  Open font_dropdown.png and check the red dot sits on the "
                  "EasyType-L=10mm row. Send it to me.")
        return 0 if ok else 2
    if args.set_font_image is not None:
        return _do_font_image_test(args.set_font_image, args.target)
    if args.set_font is not None:
        return _do_font_test(value=args.set_font)
    return _selftest()


if __name__ == "__main__":
    sys.exit(main())
