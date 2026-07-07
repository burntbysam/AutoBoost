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
   Properties panel showing "More..." is visible. Leaving the Home screen open
   too is fine -- both Boost windows get dumped.
3. Run WITHOUT passing a title on the command line:
       python tools/probe_uia.py > uia_dump.txt 2>&1
   (The default matches "TruTops Boost" and console/terminal windows are
   excluded. Do NOT pass --title with the window name: Windows Terminal echoes
   your command line into its own title bar, so a title like "Boost - Design"
   would match the terminal instead of Boost -- which is exactly what happened
   before.)

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


# Console/terminal window classes to exclude so the probe never dumps itself.
_TERMINAL_CLASSES = {
    "CASCADIA_HOSTING_WINDOW_CLASS",   # Windows Terminal
    "ConsoleWindowClass",              # classic conhost
    "PseudoConsoleWindow",
}

_ELEMENT_CAP = 4000  # stop after this many nodes so the dump stays manageable

# Where the tree dump is written. Prompts/countdown always go to the real
# console (sys.stdout); with --out the tree goes to a file so the console
# countdown stays visible even though the dump is captured.
_OUT = sys.stdout


def _walk(info, depth: int, max_depth: int, max_children: int, counter: list[int]) -> None:
    if counter[0] >= _ELEMENT_CAP:
        return
    print(f"{'  ' * depth}- {_fmt(info)}", file=_OUT)
    counter[0] += 1
    if depth >= max_depth:
        return
    try:
        children = info.children()
    except Exception as exc:  # noqa: BLE001 - diagnostic tool
        print(f"{'  ' * (depth + 1)}<could not read children: {exc!r}>", file=_OUT)
        return
    for child in children[:max_children]:
        _walk(child, depth + 1, max_depth, max_children, counter)
    if len(children) > max_children:
        print(f"{'  ' * (depth + 1)}... (+{len(children) - max_children} more siblings)")


def _find_subtrees(info, needle: str, found: list, max_depth: int, depth: int = 0):
    """Collect (up to 6) elements whose name or automation_id contains `needle`.

    Does not descend into a match (avoids nesting duplicate subtrees).
    """
    if len(found) >= 6 or depth > max_depth:
        return found
    try:
        name = (info.name or "").lower()
    except Exception:
        name = ""
    try:
        auto = (info.automation_id or "").lower()
    except Exception:
        auto = ""
    if needle in name or needle in auto:
        found.append(info)
        return found
    try:
        children = info.children()
    except Exception:
        children = []
    for child in children:
        _find_subtrees(child, needle, found, max_depth, depth + 1)
        if len(found) >= 6:
            break
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump Boost's UIA control tree.")
    parser.add_argument("--title", default="TruTops Boost",
                        help="Substring of the target window title.")
    parser.add_argument("--depth", type=int, default=9,
                        help="Maximum tree depth to print (default: 9).")
    parser.add_argument("--max-children", type=int, default=60,
                        help="Max children printed per node (default: 60).")
    parser.add_argument("--find", default=None,
                        help="Dump only subtrees whose name or automation_id "
                             "contains this substring (case-insensitive). Use to "
                             "isolate one panel, e.g. --find toolOptionsSideBar.")
    parser.add_argument("--all", action="store_true",
                        help="Dump every non-terminal top-level window (ignore "
                             "--title). Use to catch transient popups/dropdowns "
                             "that appear as their own window.")
    parser.add_argument("--delay", type=float, default=0.0,
                        help="Seconds to wait before capturing, so you can open "
                             "and hold a dropdown in Boost first.")
    parser.add_argument("--out", default=None,
                        help="Write the tree to this file (the countdown still "
                             "shows on screen). Use with --delay instead of a "
                             "shell '>' redirect so you can see the countdown.")
    args = parser.parse_args()
    # When focusing on a subtree, dig deeper by default (property grids nest).
    if args.find and args.depth < 14:
        args.depth = 14

    try:
        from pywinauto import Desktop
    except ImportError:
        print("pywinauto is not installed. Install it (no admin needed):")
        print("    pip install --user pywinauto")
        return 2

    import os
    import time
    own_pid = os.getpid()

    # Route the tree to a file if requested; keep prompts on the console.
    global _OUT
    out_file = open(args.out, "w", encoding="utf-8") if args.out else None
    if out_file:
        _OUT = out_file

    if args.delay > 0:
        print(f"Capturing in {args.delay:.0f}s -- switch to Boost now and open "
              f"(and hold) the dropdown you want captured...", flush=True)
        remaining = args.delay
        while remaining > 0:
            print(f"  {remaining:.0f}...", end=" ", flush=True)
            time.sleep(min(1.0, remaining))
            remaining -= 1.0
        print("capturing.", flush=True)

    desktop = Desktop(backend="uia")
    header = ("Dumping ALL non-terminal top-level windows"
              if args.all else
              f"Searching for top-level windows matching title ~ '{args.title}'")
    print(f"{header} (excluding console/terminal windows) ...\n", file=_OUT)

    matches = []
    for win in desktop.windows():
        try:
            title = win.window_text() or ""
        except Exception:
            title = ""
        try:
            cls = win.class_name() or ""
        except Exception:
            cls = ""
        try:
            pid = win.process_id()
        except Exception:
            pid = None
        # Never match our own console or any terminal/console host window.
        if pid == own_pid or cls in _TERMINAL_CLASSES or "probe_uia" in title.lower():
            continue
        if args.all or args.title.lower() in title.lower():
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
        print("=" * 78, file=_OUT)
        print(f"WINDOW: {title!r}", file=_OUT)
        print("=" * 78, file=_OUT)
        counter = [0]
        try:
            if args.find:
                roots = _find_subtrees(win.element_info, args.find.lower(), [], 30)
                if not roots:
                    print(f"  No element matched --find {args.find!r} in this window.", file=_OUT)
                for root in roots:
                    print(f"  --- subtree matching {args.find!r} ---", file=_OUT)
                    _walk(root, 1, args.depth, args.max_children, counter)
            else:
                _walk(win.element_info, 0, args.depth, args.max_children, counter)
        except Exception as exc:  # noqa: BLE001 - diagnostic tool
            print(f"  Could not walk tree: {exc!r}", file=_OUT)
        capped = " (element cap reached; tree truncated)" if counter[0] >= _ELEMENT_CAP else ""
        print(f"\n[{counter[0]} elements printed for this window{capped}]\n", file=_OUT)

    print("Done. Send the full output back to continue.", file=_OUT)
    if out_file:
        out_file.close()
        print(f"Wrote dump to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
