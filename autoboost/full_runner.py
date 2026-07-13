"""Combined job runner: stencil AND cut every part in the Home list, in one pass.

For each part it finishes the whole part before moving on -- stencil the
part-number, then create + apply its cutting program -- via `full_cycle`. Mirrors
the two single-process runners (duplicate guard, consecutive-failure auto-stop,
best-effort recover to Home so one bad part doesn't wedge the run), so the three
runners behave identically apart from which phases they do:

    py -m autoboost.stencil_runner # stencil only (batch)
    py -m autoboost.cut_runner     # cut only (batch)
    py -m autoboost.full_runner    # stencil + cut, per part (batch)   <-- this

    py -m autoboost.full_runner --parts 8604305I-1 8604302I-1
    py -m autoboost.full_runner --stencil-only     # == runner, via this loop
    py -m autoboost.full_runner --cut-only         # == cut_runner, via this loop

Kill switch: Ctrl+C in the terminal, or hold 'q' (needs the keyboard package).
"""

from __future__ import annotations

import argparse
import sys
import time

from .navigator.boost_uia import BoostUIA
from .full_cycle import process_full_part
from .stencil_runner import _stop_requested


def _recover_to_home(boost: BoostUIA, log=print) -> None:
    """Best effort: get back to Home so the next part can start, whichever window
    a failed cycle left open. A stuck Cut window closes with Alt+F4; a stuck
    Design view closes with '3'."""
    try:
        import pyautogui
        pyautogui.press("esc")
        time.sleep(0.3)
        boost.reset()
        if boost.has_cut():
            pyautogui.hotkey("alt", "f4")
            time.sleep(1.5)
            boost.reset()
        if boost.has_design():
            pyautogui.press("3")   # close Design view
            time.sleep(2.0)
            boost.reset()
        log("  (recovered to Home)")
    except Exception:
        pass


def run_full_job(part_names: list[str] | None = None,
                 target_font: str = "EasyType-L=10mm",
                 angular: str | None = None,
                 do_stencil: bool = True,
                 do_cut: bool = True,
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
    phases = "+".join(p for p, on in (("stencil", do_stencil), ("cut", do_cut)) if on)
    log(f"Full job: {len(names)} part(s). Phases={phases or 'none'}")

    done = skipped = consec = 0
    seen: set[str] = set()
    duplicates: list[str] = []
    for i, name in enumerate(names, 1):
        if _stop_requested():
            log("Stop requested (q) -- halting.")
            break
        log(f"\n=== [{i}/{len(names)}] {name} ===")

        # Duplicate guard: an exact part number that recurs in the sequence is
        # processed once; skip the recurrence and flag it in the summary.
        if name in seen:
            duplicates.append(name)
            log("  DUPLICATE of an earlier part -- skipping (flagged for summary)")
            continue
        seen.add(name)

        boost.reset()
        try:
            ok = process_full_part(name, target_font=target_font, angular=angular,
                                   do_stencil=do_stencil, do_cut=do_cut,
                                   log=lambda m: log("  " + m), boost=boost)
        except Exception as exc:  # noqa: BLE001 - keep the job alive
            log(f"  cycle error: {exc!r}")
            ok = False

        if ok:
            done += 1
            consec = 0
            log(f"  OK  (done={done} skipped={skipped})")
        else:
            skipped += 1
            consec += 1
            log(f"  SKIP (done={done} skipped={skipped})")
            _recover_to_home(boost, log)
            if consec >= max_consecutive_failures:
                log("Too many consecutive failures -- stopping."); break

    log(f"\nFull job complete: done={done}, skipped={skipped}, of {len(names)}")
    if duplicates:
        from collections import Counter
        counts = Counter(duplicates)
        log(f"\n*** FLAG: {len(duplicates)} duplicate part number(s) skipped "
            f"(processed once, not repeated):")
        for dup, n in counts.items():
            extra = f" (appeared {n + 1}x)" if n > 1 else ""
            log(f"      - {dup}{extra}")
    return skipped == 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stencil AND cut every part in the Home list, in one pass.")
    ap.add_argument("--parts", nargs="*", default=None,
                    help="Specific part names to process (default: all in the list).")
    ap.add_argument("--font", default="EasyType-L=10mm", help="Target font value.")
    ap.add_argument("--angular", default=None,
                    help="'Allowed angular positions (Job)' value. Omit for the "
                         "LAST option ('0°;90°...'); pass e.g. '0°;90°' by name.")
    ap.add_argument("--stencil-only", action="store_true",
                    help="Run only the stencil phase for every part (skip cutting).")
    ap.add_argument("--cut-only", action="store_true",
                    help="Run only the cutting phase for every part (skip stenciling).")
    ap.add_argument("--max-failures", type=int, default=5,
                    help="Stop after this many consecutive failures (default: 5).")
    args = ap.parse_args()

    if args.stencil_only and args.cut_only:
        print("Choose at most one of --stencil-only / --cut-only.")
        return 2

    print("Starting in 5s -- put Boost on the Home screen. Ctrl+C or 'q' to stop.")
    time.sleep(5)
    try:
        run_full_job(part_names=args.parts, target_font=args.font,
                     angular=args.angular,
                     do_stencil=not args.cut_only,
                     do_cut=not args.stencil_only,
                     max_consecutive_failures=args.max_failures)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
