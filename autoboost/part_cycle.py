"""Per-part cycle: the in-Design sequence, assembled from the proven pieces.

Assumes a part is already open in Design view with the part-number tool's
hotkeys available (Z=zoom extents, 1=part-number tool, 2=save, 3=close). Chains:

    focus Boost -> Z (zoom extents) -> compute safe placement (vision) ->
    1 + click to place text -> Esc -> click the point to select the text ->
    add Font type (if missing) -> set EasyType-L=10mm (drag) ->
    [2 save -> verify] -> [3 close]

Run it on ONE open part first, watching, with save/close OFF so it's reversible:

    py -m autoboost.part_cycle
    py -m autoboost.part_cycle --save            # also save + verify
    py -m autoboost.part_cycle --save --close     # full cycle

Each step logs, so a failure points at exactly one action.
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import DEFAULT
from .navigator.boost_uia import BoostUIA


def _shot_bgr():
    import numpy as np
    import cv2
    import pyautogui
    return cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2BGR)


def _canvas_rect(boost) -> tuple[int, int, int, int]:
    """Design-View drawing canvas in screen pixels, derived from the window rect
    and the known panel offsets (left dock ~300, ribbon ~190, status ~45)."""
    r = boost.design().wrapper_object().rectangle()
    return (r.left + 300, r.top + 190, r.right - 10, r.bottom - 45)


def process_open_part(target_font: str = "EasyType-L=10mm",
                      do_save: bool = False,
                      do_close: bool = False,
                      log=print,
                      boost: BoostUIA | None = None) -> bool:
    import pyautogui
    from .vision.placement import find_safe_placement
    from .vision.verify import verify_placement

    t = DEFAULT.timing
    boost = boost or BoostUIA()
    if not boost.has_design():
        log("No Design window open. Open a part in Design view first.")
        return False

    # Focus Boost so the hotkeys don't go to the terminal.
    try:
        boost.design().wrapper_object().set_focus()
    except Exception:
        pass
    time.sleep(0.3)

    # 1. Zoom extents.
    pyautogui.press("z")
    time.sleep(t.after_zoom)
    log("zoom extents (z)")

    # 2. Safe placement from the current canvas. Keep this clean pre-placement
    #    frame -- verify diffs against it later (the marking is the difference).
    rect = _canvas_rect(boost)
    clean = _shot_bgr()
    res = find_safe_placement(clean, DEFAULT, rect)
    log(f"placement: point={res.point} clearance={res.clearance_px:.0f}px "
        f"ok={res.ok} ({res.reason})")
    if res.point is None:
        log("no safe placement found -- aborting part")
        return False
    px, py = res.point

    # 3. Place the part-number text: activate tool, click, then fully exit the
    #    tool with Esc (twice -- one press sometimes doesn't drop the command, and
    #    a lingering tool would make the next click place a SECOND number instead
    #    of selecting).
    pyautogui.press("1")
    time.sleep(t.after_tool_activate)
    pyautogui.click(px, py)
    time.sleep(t.after_place_click)
    pyautogui.press("esc")
    time.sleep(t.after_esc)
    pyautogui.press("esc")
    time.sleep(t.after_esc)
    log(f"placed part-number text at ({px},{py}); tool exited")

    # 4. Select the placed text. Boost ignores a click on the exact same pixel
    #    just used to place, so nudge a few px -- still on the text.
    sx, sy = px + 3, py + 3
    pyautogui.click(sx, sy)
    time.sleep(t.after_panel_open)
    log(f"selected placed text (click at {sx},{sy})")

    # 5. Font: add the Font type property if absent, then set EasyType-L=10mm.
    if "Font type" not in boost._grid_controls()["buttons"]:
        added = boost.add_font_type()
        log(f"add Font type -> {added} ({boost.last_value})")
        if not added:
            log("could not add Font type -- aborting part")
            return False
    set_ok = boost.set_font_by_drag(target_font)
    log(f"set font -> {set_ok} ({boost.last_value})")
    if not set_ok:
        log("could not set font -- aborting part")
        return False

    # 6. Save + verify (optional).
    if do_save:
        # Exit the Properties menu, then click empty part-body to DESELECT the
        # text box (Esc alone leaves it selected, so '2' would act on it, not
        # save). The placement clearance is the radius that is guaranteed on the
        # part (no edge/hole), so a click just inside it -- offset off the text
        # -- is safe dead-space.
        pyautogui.press("esc")
        time.sleep(t.after_esc)
        off = max(25, min(60, int(res.clearance_px) - 10))
        dead_x, dead_y = px, py + off
        pyautogui.click(dead_x, dead_y)
        time.sleep(0.4)
        log(f"deselected text (click dead-space at {dead_x},{dead_y})")
        pyautogui.press("2")
        time.sleep(t.after_save)
        post = _shot_bgr()
        log("saved (2)")
        # Verify against the CLEAN pre-placement frame: the marking is the diff
        # (the text is already full-size before save, so a pre/post-save diff
        # sees no change -- that was the false FAIL).
        v = verify_placement(clean, post, DEFAULT, rect)
        log(f"verify -> {'PASS' if v.ok else 'FAIL'} ({v.reason})")

    # 7. Close Design view (optional).
    if do_close:
        pyautogui.press("3")
        time.sleep(t.after_close)
        log("closed Design view (3)")

    log("part cycle complete")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the in-Design part cycle on the open part.")
    ap.add_argument("--font", default="EasyType-L=10mm", help="Target font value.")
    ap.add_argument("--save", action="store_true", help="Also press 2 to save and verify.")
    ap.add_argument("--close", action="store_true", help="Also press 3 to close Design view.")
    args = ap.parse_args()
    try:
        ok = process_open_part(args.font, do_save=args.save, do_close=args.close)
    except ImportError as exc:
        print(f"Missing dependency: {exc}. Run: pip install --user pywinauto opencv-python numpy pyautogui")
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
