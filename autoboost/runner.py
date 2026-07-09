"""Job runner: apply the part cycle to every part in the Home list.

For each part: open it into Design view, run the in-Design cycle
(place -> font -> save -> verify), close back to Home, move on. Tracks
done/skipped, stops after too many consecutive failures, and always tries to
return to Home on error so one bad part doesn't wedge the whole run.

    py -m autoboost.runner                     # all parts, save + close
    py -m autoboost.runner --parts 8604305I-1 8604302I-1
    py -m autoboost.runner --no-save --no-close    # dry mechanics test

Kill switch: Ctrl+C in the terminal, or hold 'q' (needs the keyboard package).
"""

from __future__ import annotations

import argparse
import sys
import time

from .navigator.boost_uia import BoostUIA
from .part_cycle import process_open_part


def _stop_requested() -> bool:
    try:
        import keyboard
        return keyboard.is_pressed("q")
    except Exception:
        return False


def _recover_to_home(log=print) -> None:
    """Best effort: close the Design view so the next part can open."""
    try:
        import pyautogui
        pyautogui.press("esc")
        time.sleep(0.3)
        pyautogui.press("3")   # close Design view
        time.sleep(2.0)
        log("  (recovered to Home)")
    except Exception:
        pass


def run_job(part_names: list[str] | None = None,
            target_font: str = "EasyType-L=10mm",
            do_save: bool = True,
            do_close: bool = True,
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
    log(f"Job: {len(names)} part(s). Save={do_save} Close={do_close}")

    done = skipped = consec = 0
    for i, name in enumerate(names, 1):
        if _stop_requested():
            log("Stop requested (q) -- halting.")
            break
        log(f"\n=== [{i}/{len(names)}] {name} ===")
        boost.reset()

        if not boost.open_part_in_design(name):
            log(f"  open failed: {boost.last_value} -- skipping")
            skipped += 1
            consec += 1
            if consec >= max_consecutive_failures:
                log("Too many consecutive failures -- stopping."); break
            continue

        try:
            ok = process_open_part(target_font, do_save=do_save,
                                   do_close=do_close, log=lambda m: log("  " + m),
                                   boost=boost)
        except Exception as exc:  # noqa: BLE001 - keep the job alive
            log(f"  cycle error: {exc!r}")
            ok = False

        if ok:
            done += 1
            consec = 0
            log(f"  OK  (done={done} skipped={skipped})")
            if not do_close:
                _recover_to_home(log)   # still need Home for the next part
        else:
            skipped += 1
            consec += 1
            log(f"  SKIP (done={done} skipped={skipped})")
            _recover_to_home(log)
            if consec >= max_consecutive_failures:
                log("Too many consecutive failures -- stopping."); break

    log(f"\nJob complete: done={done}, skipped={skipped}, of {len(names)}")
    return skipped == 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the AutoBoost job over the Home parts list.")
    ap.add_argument("--parts", nargs="*", default=None,
                    help="Specific part names to process (default: all in the list).")
    ap.add_argument("--font", default="EasyType-L=10mm", help="Target font value.")
    ap.add_argument("--no-save", action="store_true", help="Do not save each part.")
    ap.add_argument("--no-close", action="store_true", help="Do not close Design after each part.")
    ap.add_argument("--max-failures", type=int, default=5,
                    help="Stop after this many consecutive failures (default: 5).")
    args = ap.parse_args()

    print("Starting in 5s -- put Boost on the Home screen. Ctrl+C or 'q' to stop.")
    time.sleep(5)
    try:
        run_job(part_names=args.parts, target_font=args.font,
                do_save=not args.no_save, do_close=not args.no_close,
                max_consecutive_failures=args.max_failures)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
