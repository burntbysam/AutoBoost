"""Create and apply a cutting program for a part, driven from the Home screen.

The cutting-program flow hangs off every part's Home detail page, the same
place the part-number Design view is opened from -- but instead of the Design
row's 'Open' we use the Cutting Programs row's 'New'. The full cycle:

    (select part) -> Cutting Programs 'New' -> set 'Allowed angular positions
    (Job)' -> the program row's 'Open' -> the Cut window opens
    -> auto-apply cutting technology -> wait -> Esc (dismiss notice)
    -> Ctrl+S (save) -> Alt+F4 (close) -> back to Home.

The Home half is UIA (buttons + a real WPF combo). The Cut window is a Qt app
whose ribbon is invisible to UIA, so the auto-apply button is a positional
click (config.cut.apply_button_offset) and the rest is keyboard.

    py -m autoboost.cut_cycle                       # currently selected part
    py -m autoboost.cut_cycle --part 8604300I-1
    py -m autoboost.cut_cycle --no-finish           # open the Cut window only
    py -m autoboost.cut_cycle --angular "0°;90°"    # pick a value by name
    py -m autoboost.cut_cycle --locate              # read-only: report controls

Run --locate first if anything on the Home side looks off: it clicks nothing
and prints the exact auto_ids.
"""

from __future__ import annotations

import argparse
import sys
import time

from .navigator.boost_uia import BoostUIA


def create_cut_program(part_name: str | None = None,
                       angular: str | None = None,
                       log=print,
                       boost: BoostUIA | None = None) -> bool:
    """Select `part_name` (or use the current selection) and create + open a
    cutting program with the given angular positions (opens the Cut window)."""
    boost = boost or BoostUIA()
    if not boost.has_home():
        log("HomeZone window not found. Put Boost on the Home screen first.")
        return False

    if part_name is not None:
        if not boost.select_part(part_name):
            log(f"part {part_name!r} not found in the Home list")
            return False
        time.sleep(0.6)
        log(f"selected part {part_name}")

    return boost.create_cut_program(angular, log=lambda m: log("  " + m))


def process_cut(part_name: str | None = None,
                angular: str | None = None,
                do_finish: bool = True,
                log=print,
                boost: BoostUIA | None = None) -> bool:
    """Full per-part cutting cycle: create + open the Cut window, then (unless
    do_finish is False) auto-apply the technology, save, and close to Home."""
    boost = boost or BoostUIA()
    if not create_cut_program(part_name, angular, log=log, boost=boost):
        log(f"cut create failed ({boost.last_value})")
        return False
    log("cut program open")
    if not do_finish:
        log("cut cycle complete (open only; --no-finish)")
        return True
    ok = boost.finish_cut_program(log=lambda m: log("  " + m))
    log("cut cycle complete" if ok else f"cut finish failed ({boost.last_value})")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Create + apply a cutting program for a part from Home.")
    ap.add_argument("--part", default=None,
                    help="Part to select first (default: the current selection).")
    ap.add_argument("--angular", default=None,
                    help="'Allowed angular positions (Job)' value. Omit to pick "
                         "the LAST option ('0°;90°...'); pass e.g. '0°;90°' by name.")
    ap.add_argument("--no-finish", action="store_true",
                    help="Stop after opening the Cut window (skip auto-apply / "
                         "save / close).")
    ap.add_argument("--locate", action="store_true",
                    help="Read-only: report the cutting-program controls and "
                         "their auto_ids. Clicks nothing.")
    args = ap.parse_args()

    try:
        boost = BoostUIA()
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Run: pip install --user pywinauto")
        return 2

    if not boost.has_home():
        print("HomeZone window not found. Is Boost on the Home screen?")
        return 1

    if args.locate:
        print(boost.locate_cut_controls())
        return 0

    t0 = time.time()
    ok = process_cut(args.part, args.angular,
                     do_finish=not args.no_finish, boost=boost)
    print(f"result -> {ok}   ({time.time() - t0:.1f}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
