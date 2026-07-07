"""Probe whether TRUMPF TruTops Boost exposes a UI Automation (UIA) tree.

WHY THIS MATTERS
----------------
AutoBoost's biggest reliability risk is driving Boost's menus/Properties panel by
matching PNG templates on screen -- that is what produced the "clicked 'Mode'
instead of 'More...'" failures, and it degrades with RDP compression/scaling.

Windows UI Automation reads the application's actual control tree (button names,
automation ids, control types) *server-side*, independent of how pixels render.
If Boost exposes a useful UIA tree, we can click "More...", "Add", and select the
font by control identity -- immune to RDP blur and resolution changes. If Boost's
UI is an opaque rendered canvas with no UIA controls, we stay on vision.

This script answers that question. It does NOT change anything in Boost.

WINDOW TITLES (from observation)
--------------------------------
  Home screen  : "TruTops Boost - HomeZone"
  Design view  : "<part> - TruTops Boost - Design"   (title starts with the part)
So to target the Design view, filter on the stable substring "Boost - Design".

HOW TO RUN
----------
1. On the RDP workstation, same session as Boost:
       pip install --user pywinauto
2. Open the Design view of a part, select the placed part-number text so the
   Properties panel showing "More..." is visible.
3. Run (note forward slashes -- they avoid the backslash-escape trap):
       python tools/probe_uia.py --title "Boost - Design" --depth 9 > uia_dump.txt 2>&1
   For the Home screen instead:
       python tools/probe_uia.py --title "HomeZone" --depth 9 > uia_dump_home.txt 2>&1

WHAT TO SEND BACK
-----------------
Send the whole output file. Named Button / MenuItem / List / Edit controls --
especially anything like "More", "Add", "Font type", or the "Dimensions" field --
mean UIA navigation is viable. A tree that is essentially empty (just a top-level
window and a single "pane"/custom render surface) means we stay on vision.
"""

from __future__ import annotations

import argparse
import sys


def _fmt(info) -> str:
    """One-line description of a UIA element, tolerant of missing attributes."""
    def safe(getter, default=""):
        try:
            val = getter()
            return val if val is not None else default
        except Exception:
            return default

    ctrl = safe(lambda: info.control_type, "?")
    name = safe(lambda: info.name, "")
    auto = safe(lambda: info.automation_id, "")
    cls = safe(lambda: info.class_name, "")
    try:
        r = info.rectangle
        rect = f"({r.left},{r.top},{r.right},{r.bottom})"
    except Exception:
        rect = ""
    parts = [f"{ctrl}"]
    if name:
        parts.append(f"name={name!r}")
    if auto:
        parts.append(f"auto_id={auto!r}")
    if cls:
        parts.append(f"class={cls!r}")
    if rect:
        parts.append(rect)
    return "  ".join(parts)


def _walk(info, depth: int, max_depth: int, max_children: int, counter: list[int]) -> None:
    print(f"{'  ' * depth}- {_fmt(info)}")
    counter[0] += 1
    if depth >= max_depth:
        return
    try:
        children = info.children()
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"{'  ' * (depth + 1)}<could not read children: {exc!r}>")
        return
    for child in children[:max_children]:
        _walk(child, depth + 1, max_depth, max_children, counter)
    if len(children) > max_children:
        print(f"{'  ' * (depth + 1)}... (+{len(children) - max_children} more siblings)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump Boost's UIA control tree.")
    parser.add_argument("--title", default="TruTops Boost",
                        help="Substring of the target window title.")
    parser.add_argument("--depth", type=int, default=9,
                        help="Maximum tree depth to print (default: 9).")
    parser.add_argument("--max-children", type=int, default=60,
                        help="Max children printed per node (default: 60).")
    args = parser.parse_args()

    try:
        from pywinauto import Desktop
    except ImportError:
        print("pywinauto is not installed. Install it (no admin needed):")
        print("    pip install --user pywinauto")
        return 2

    desktop = Desktop(backend="uia")
    print(f"Searching for top-level windows matching title ~ '{args.title}' ...\n")

    matches = []
    for win in desktop.windows():
        try:
            title = win.window_text()
        except Exception:
            title = ""
        if args.title.lower() in (title or "").lower():
            matches.append((title, win))

    if not matches:
        print("No matching window found. Open top-level windows were:")
        for win in desktop.windows():
            try:
                print(f"  - {win.window_text()!r}  [class={win.class_name()!r}]")
            except Exception:
                pass
        print("\nRe-run with --title <substring> from the list above.")
        return 1

    for title, win in matches:
        print("=" * 78)
        print(f"WINDOW: {title!r}")
        print("=" * 78)
        counter = [0]
        try:
            _walk(win.element_info, 0, args.depth, args.max_children, counter)
        except Exception as exc:  # noqa: BLE001 - diagnostic tool
            print(f"  Could not walk tree: {exc!r}")
        print(f"\n[{counter[0]} elements printed for this window]\n")

    print("Done. Send the full output back to continue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
