"""Combined per-part cycle: stencil the part-number, then cut it -- in one pass.

Beta 0.7.0 fuses the two validated tools into a single per-part procedure. When
the run arrives on a part it finishes the *entire* part before advancing:

    (from Home) open in Design -> place part-number -> set font -> save ->
    verify -> close to Home            [stencil, via part_cycle]
    -> create cutting program -> set angular positions -> open the Cut window ->
    auto-apply technology -> save -> close to Home   [cutting, via cut_cycle]

Both halves start and end on the Home screen, so they compose cleanly: the
stencil closes Design back to Home, then the cut half re-selects the same part
from Home and builds its program. The cut half only runs if the stencil
succeeded -- a part that couldn't be stenciled is not cut (it's skipped whole
and flagged), so we never leave a half-finished part behind.

    py -m autoboost.full_cycle --part 8604300I-1        # one part, both phases
    py -m autoboost.full_cycle --part 8604300I-1 --stencil-only
    py -m autoboost.full_cycle --part 8604300I-1 --cut-only

For a whole job use `full_runner`.
"""

from __future__ import annotations

import argparse
import sys
import time

from .navigator.boost_uia import BoostUIA
from .part_cycle import process_open_part
from .cut_cycle import process_cut


def process_full_part(part_name: str,
                      target_font: str = "EasyType-L=10mm",
                      angular: str | None = None,
                      do_stencil: bool = True,
                      do_cut: bool = True,
                      log=print,
                      boost: BoostUIA | None = None) -> bool:
    """Stencil then cut a single part, both driven from Home.

    Returns True only if every requested phase succeeded. The cut phase is
    attempted only when the stencil phase succeeded (or was skipped), so a part
    is never cut after a failed stencil.
    """
    boost = boost or BoostUIA()
    if not boost.has_home():
        log("HomeZone window not found. Put Boost on the Home screen first.")
        return False

    # --- Phase 1: stencil (open in Design, place + font + save + verify, close).
    if do_stencil:
        boost.reset()
        if not boost.open_part_in_design(part_name):
            log(f"stencil: open failed ({boost.last_value})")
            return False
        try:
            ok = process_open_part(target_font, do_save=True, do_close=True,
                                   log=lambda m: log("  " + m), boost=boost)
        except Exception as exc:  # noqa: BLE001 - report, let the caller recover
            log(f"stencil: cycle error: {exc!r}")
            return False
        if not ok:
            log("stencil: failed -- not cutting this part")
            return False
        log("stencil complete")

    # --- Phase 2: cutting (re-select from Home, create program, apply, close).
    if do_cut:
        boost.reset()
        try:
            ok = process_cut(part_name, angular=angular, do_finish=True,
                             log=lambda m: log("  " + m), boost=boost)
        except Exception as exc:  # noqa: BLE001
            log(f"cut: cycle error: {exc!r}")
            return False
        if not ok:
            log(f"cut: failed ({boost.last_value})")
            return False
        log("cut complete")

    log("part fully processed")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Stencil then cut one part, from the Home screen.")
    ap.add_argument("--part", required=True, help="Part to process.")
    ap.add_argument("--font", default="EasyType-L=10mm", help="Target font value.")
    ap.add_argument("--angular", default=None,
                    help="'Allowed angular positions (Job)' value. Omit for the "
                         "LAST option ('0°;90°...'); pass e.g. '0°;90°' by name.")
    ap.add_argument("--stencil-only", action="store_true",
                    help="Run only the stencil phase (skip cutting).")
    ap.add_argument("--cut-only", action="store_true",
                    help="Run only the cutting phase (skip stenciling).")
    args = ap.parse_args()

    if args.stencil_only and args.cut_only:
        print("Choose at most one of --stencil-only / --cut-only.")
        return 2

    try:
        boost = BoostUIA()
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Run: pip install --user pywinauto")
        return 2
    if not boost.has_home():
        print("HomeZone window not found. Is Boost on the Home screen?")
        return 1

    t0 = time.time()
    ok = process_full_part(args.part, target_font=args.font, angular=args.angular,
                           do_stencil=not args.cut_only,
                           do_cut=not args.stencil_only, boost=boost)
    print(f"result -> {ok}   ({time.time() - t0:.1f}s)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
