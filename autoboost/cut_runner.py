"""Cutting-program job runner: apply the cut cycle to every part in Home.

For each part: create a cutting program, set the angular positions, open the
Cut window, auto-apply the cutting technology, save, and close back to Home,
then move on. Mirrors the stencil runner -- duplicate guard, consecutive-
failure auto-stop, and a best-effort recover so one bad part doesn't wedge the
run.

    py -m autoboost.cut_runner                        # all parts in the Home list
    py -m autoboost.cut_runner --parts 8604305I-1 8604302I-1
    py -m autoboost.cut_runner --no-finish            # open each Cut window only

Kill switch: Ctrl+C in the terminal, or hold 'q' (needs the keyboard package).
"""

from __future__ import annotations

import argparse
import sys
import time

from .navigator.boost_uia import BoostUIA
from .cut_cycle import process_cut
from .runner import _stop_requested


def _recover_to_home(boost: BoostUIA, log=print) -> None:
    """Best effort: get back to Home so the next part can start. If a Cut window
    is still open from a failed cycle, close it (Alt+F4)."""
    try:
        import pyautogui
        pyautogui.press("esc")
        time.sleep(0.3)
        boost.reset()
        if boost.has_cut():
            pyautogui.hotkey("alt", "f4")
            time.sleep(1.5)
            boost.reset()
        log("  (recovered to Home)")
    except Exception:
        pass


def run_cut_job(part_names: list[str] | None = None,
                angular: str | None = None,
                do_finish: bool = True,
                max_consecutive_failures: int = 5,
                log=print) -> bool:
    boost = BoostUIA()
    if not boost.has_home():
        log("HomeZone window not found. Put Boost on the Home screen and retry.")
        return False

    names = part_names or [p["name"] for p in boost.parts()]
    if not names:
        log("No parts found in the Home list.")
        return False
    log(f"Cut job: {len(names)} part(s). Finish={do_finish}")

    done = skipped = consec = 0
    seen: set[str] = set()
    duplicates: list[str] = []
    for i, name in enumerate(names, 1):
        if _stop_requested():
            log("Stop requested (q) -- halting.")
            break
        log(f"\n=== [{i}/{len(names)}] {name} ===")

        # Duplicate guard: an exact part number that recurs in the sequence gets
        # a cutting program once; skip the recurrence and flag it in the summary.
        if name in seen:
            duplicates.append(name)
            log("  DUPLICATE of an earlier part -- skipping (flagged for summary)")
            continue
        seen.add(name)

        boost.reset()
        try:
            ok = process_cut(name, angular=angular, do_finish=do_finish,
                             log=lambda m: log("  " + m), boost=boost)
        except Exception as exc:  # noqa: BLE001 - keep the job alive
            log(f"  cycle error: {exc!r}")
            ok = False

        if ok:
            done += 1
            consec = 0
            log(f"  OK  (done={done} skipped={skipped})")
            if not do_finish:
                _recover_to_home(boost, log)   # Cut window left open; close it
        else:
            skipped += 1
            consec += 1
            log(f"  SKIP (done={done} skipped={skipped})")
            _recover_to_home(boost, log)
            if consec >= max_consecutive_failures:
                log("Too many consecutive failures -- stopping."); break

    log(f"\nCut job complete: done={done}, skipped={skipped}, of {len(names)}")
    if duplicates:
        from collections import Counter
        counts = Counter(duplicates)
        log(f"\n*** FLAG: {len(duplicates)} duplicate part number(s) skipped "
            f"(cut once, not re-created):")
        for dup, n in counts.items():
            extra = f" (appeared {n + 1}x)" if n > 1 else ""
            log(f"      - {dup}{extra}")
    return skipped == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the AutoBoost cutting job over the Home parts list.")
    ap.add_argument("--parts", nargs="*", default=None,
                    help="Specific part names to process (default: all in the list).")
    ap.add_argument("--angular", default=None,
                    help="'Allowed angular positions (Job)' value. Omit for the "
                         "LAST option ('0°;90°...'); pass e.g. '0°;90°' by name.")
    ap.add_argument("--no-finish", action="store_true",
                    help="Open each Cut window only (skip apply/save/close).")
    ap.add_argument("--max-failures", type=int, default=5,
                    help="Stop after this many consecutive failures (default: 5).")
    args = ap.parse_args()

    print("Starting in 5s -- put Boost on the Home screen. Ctrl+C or 'q' to stop.")
    time.sleep(5)
    try:
        run_cut_job(part_names=args.parts, angular=args.angular,
                    do_finish=not args.no_finish,
                    max_consecutive_failures=args.max_failures)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
