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

HOW TO RUN
----------
1. On the RDP workstation, in the same session where Boost is visible, install
   pywinauto (per-user, no admin needed):
       pip install --user pywinauto
2. Open Boost and navigate to the state you care about most -- ideally Design
   View with a part's Properties panel showing the "More..." affordance -- so
   those controls are present in the tree.
3. Run:
       python probe_uia.py
   To target a specific window or go deeper:
       python probe_uia.py --title Boost --depth 8

WHAT TO SEND BACK
-----------------
Copy the whole console output (or redirect to a file:
    python probe_uia.py > uia_dump.txt 2>&1
and send uia_dump.txt). The presence/absence of named Button/MenuItem/List
controls -- especially anything like "More", "Add", "Font type" -- decides the
navigation architecture.
"""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump Boost's UIA control tree.")
    parser.add_argument(
        "--title", default="Boost",
        help="Substring of the target window title (default: 'Boost').",
    )
    parser.add_argument(
        "--depth", type=int, default=7,
        help="Maximum tree depth to print (default: 7).",
    )
    args = parser.parse_args()

    try:
        from pywinauto import Desktop
    except ImportError:
        print("pywinauto is not installed. Install it (no admin needed):")
        print("    pip install --user pywinauto")
        return 2

    print(f"Searching for top-level windows matching title ~ '{args.title}' ...")
    desktop = Desktop(backend="uia")

    matches = []
    for win in desktop.windows():
        try:
            title = win.window_text()
        except Exception:
            title = ""
        if args.title.lower() in (title or "").lower():
            matches.append(win)

    if not matches:
        print("No matching window found. Open windows were:")
        for win in desktop.windows():
            try:
                print(f"  - {win.window_text()!r}  [{win.class_name()}]")
            except Exception:
                pass
        print("\nRe-run with --title <substring> matching Boost's window title.")
        return 1

    for win in matches:
        print("=" * 78)
        try:
            print(f"WINDOW: {win.window_text()!r}  class={win.class_name()!r}")
        except Exception:
            print("WINDOW: <unreadable title>")
        print("=" * 78)
        try:
            # print_control_identifiers is the most informative dump: it lists
            # control_type, title, auto_id and how to reference each control.
            win.print_control_identifiers(depth=args.depth)
        except Exception as exc:  # noqa: BLE001 - diagnostic tool, report anything
            print(f"  Could not dump control identifiers: {exc!r}")
            print("  This often means the UI is a single opaque render surface")
            print("  (no child UIA controls) -> vision navigation required.")

    print("\nDone. Send the full output back to continue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
