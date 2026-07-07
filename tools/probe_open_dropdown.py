"""Deterministically open a Boost property-grid dropdown and dump it.

The font chain's dropdowns are transient: held open by hand they collapse before
a --delay probe fires, so uia_1/uia_3 only caught the in-place editor, not the
list. This tool opens the dropdown itself via UIA and dumps immediately, while it
is still open -- then presses Esc so nothing is applied (non-destructive).

From the dumps we know a selected property row exposes:
    Edit   auto_id='9765996'   (the in-place value editor)
    Button auto_id='4261530'   name='Open'  (its dropdown arrow)

Usage (Design view open, part-number text selected, Properties tab showing):
    python tools/probe_open_dropdown.py --row "Font type" --out uia_fontlist.txt
    python tools/probe_open_dropdown.py --row "More..."   --out uia_addprop.txt

Send the resulting file. It reveals whether the open list is UIA-exposed (a List
with ListItems we can select by name, e.g. 'EasyType-L=10MM') or owner-drawn (no
list items -> we drive it by typing the value into the editor instead).
"""

from __future__ import annotations

import argparse
import sys
import time

DESIGN_TITLE_RE = r".* - TruTops Boost - Design"
_TERMINAL_CLASSES = {"CASCADIA_HOSTING_WINDOW_CLASS", "ConsoleWindowClass", "PseudoConsoleWindow"}
_OPEN_BUTTON_AUTOID = "4261530"   # the in-place dropdown 'Open' arrow
_CAP = 3000


def _fmt(info) -> str:
    def safe(g):
        try:
            return g() or ""
        except Exception:
            return ""
    ctrl = safe(lambda: info.control_type) or "?"
    parts = [ctrl]
    for label, val in (("name", safe(lambda: info.name)),
                       ("auto_id", safe(lambda: info.automation_id)),
                       ("class", safe(lambda: info.class_name))):
        if val:
            parts.append(f"{label}={val!r}")
    try:
        r = info.rectangle
        parts.append(f"({r.left},{r.top},{r.right},{r.bottom})")
    except Exception:
        pass
    return "  ".join(parts)


def _walk(info, depth, max_depth, counter, out):
    if counter[0] >= _CAP:
        return
    print(f"{'  ' * depth}- {_fmt(info)}", file=out)
    counter[0] += 1
    if depth >= max_depth:
        return
    try:
        children = info.children()
    except Exception:
        return
    for c in children[:80]:
        _walk(c, depth + 1, max_depth, counter, out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Open a property-grid dropdown and dump it.")
    ap.add_argument("--row", default="Font type", help="Property row to open (default: 'Font type').")
    ap.add_argument("--out", default=None, help="Write dump to this file.")
    ap.add_argument("--depth", type=int, default=12)
    args = ap.parse_args()
    out = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout

    try:
        from pywinauto import Desktop
        from pywinauto.keyboard import send_keys
    except ImportError:
        print("pywinauto not installed. Run: pip install --user pywinauto")
        return 2

    desktop = Desktop(backend="uia")
    design = desktop.window(title_re=DESIGN_TITLE_RE, control_type="Window")
    if not design.exists(timeout=2):
        print("Design window not found. Open a part in Design view first.")
        return 1

    grid = design.child_window(auto_id="propertyGrid1")

    # 1) Select the row so its in-place editor + dropdown button appear.
    print(f"Selecting row {args.row!r} ...")
    try:
        grid.child_window(title=args.row, control_type="Button").click_input()
    except Exception as exc:
        print(f"Could not click row {args.row!r}: {exc!r}")
        return 1
    time.sleep(0.6)

    # 2) Click the dropdown 'Open' arrow to expand the list.
    opened = False
    for locator in (
        lambda: grid.child_window(auto_id=_OPEN_BUTTON_AUTOID),
        lambda: grid.child_window(title="Open", control_type="Button"),
    ):
        try:
            locator().click_input()
            opened = True
            break
        except Exception:
            continue
    print(f"Dropdown opened: {opened}")
    time.sleep(1.0)

    # 3) Dump everything while the list is still open.
    for win in desktop.windows():
        try:
            title = win.window_text() or ""
            cls = win.class_name() or ""
        except Exception:
            title, cls = "", ""
        if cls in _TERMINAL_CLASSES or "probe_" in title.lower():
            continue
        print("=" * 78, file=out)
        print(f"WINDOW: {title!r}  class={cls!r}", file=out)
        print("=" * 78, file=out)
        counter = [0]
        try:
            _walk(win.element_info, 0, args.depth, counter, out)
        except Exception as exc:
            print(f"  <walk failed: {exc!r}>", file=out)
        print(f"[{counter[0]} elements]\n", file=out)

    # 4) Cancel so nothing is applied.
    try:
        send_keys("{ESC}")
        send_keys("{ESC}")
    except Exception:
        pass

    if out is not sys.stdout:
        out.close()
        print(f"Wrote dump to {args.out}")
    print("Done (pressed Esc to cancel -- nothing applied).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
