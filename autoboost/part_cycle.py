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
import os
import sys
import time
from datetime import datetime

from . import __version__
from .config import DEFAULT
from .navigator.boost_uia import BoostUIA


def _save_debug(image, tag: str, log=print) -> None:
    """Best-effort: drop a vision debug overlay in logs/<version>/ so any odd
    placement/verify decision comes with the exact pixels that produced it."""
    if image is None:
        return
    try:
        import cv2
        dbg_dir = os.path.join("logs", __version__)
        os.makedirs(dbg_dir, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(dbg_dir, f"{tag}_{stamp}.png")
        cv2.imwrite(path, image)
        log(f"debug overlay saved: {path}")
    except Exception:
        pass


# Zoom the placement point up before selecting the (possibly tiny) text. Scroll
# zoom is cursor-anchored, so the text stays under the point while it enlarges.
SELECT_ZOOM_STEPS = 6
SELECT_SCROLL = -300   # per step; NEGATIVE zooms in on this Boost -- flip if it zooms OUT


def _shot_bgr():
    import numpy as np
    import cv2
    import pyautogui
    return cv2.cvtColor(np.array(pyautogui.screenshot()), cv2.COLOR_RGB2BGR)


def _zoom_at(px: int, py: int, steps: int, per_step: int) -> None:
    import pyautogui
    pyautogui.moveTo(px, py)
    time.sleep(0.2)
    for _ in range(steps):
        pyautogui.scroll(per_step)
        time.sleep(0.05)
    time.sleep(0.3)


def _canvas_rect(boost) -> tuple[int, int, int, int]:
    """Design-View drawing canvas in screen pixels, derived from the window rect
    and the known panel offsets (left dock ~300, ribbon ~190, status ~45)."""
    r = boost.design().wrapper_object().rectangle()
    return (r.left + 300, r.top + 190, r.right - 10, r.bottom - 45)


def _parse_dims_mm(text: str) -> tuple[float, float] | None:
    """Parse Boost's Dimensions field, e.g. '40.3 in x 12.6 in' or '18 mm x 9 mm',
    into (width, height) millimetres. Returns None if it can't be read."""
    import re
    m = re.search(r"([\d.]+)\s*(in|mm)?\s*[xX]\s*([\d.]+)\s*(in|mm)?", text or "")
    if not m:
        return None
    try:
        w = float(m.group(1))
        h = float(m.group(3))
    except ValueError:
        return None
    unit = (m.group(4) or m.group(2) or "in").lower()
    scale = 25.4 if unit == "in" else 1.0
    if w <= 0 or h <= 0:
        return None
    return w * scale, h * scale


def process_open_part(target_font: str = "EasyType-L=10mm",
                      do_save: bool = False,
                      do_close: bool = False,
                      log=print,
                      boost: BoostUIA | None = None,
                      part_name: str | None = None) -> bool:
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
    #    Feed the part's real dimensions + number length so placement reserves a
    #    rectangle matching the wide/short engraving; any read failure just falls
    #    back to the isotropic circle.
    rect = _canvas_rect(boost)
    part_dims_mm = None
    try:
        part_dims_mm = _parse_dims_mm(boost.read_dimensions())
    except Exception:
        part_dims_mm = None
    char_count = len(part_name) if part_name else None
    clean = _shot_bgr()
    res = find_safe_placement(clean, DEFAULT, rect,
                              part_dims_mm=part_dims_mm, char_count=char_count)
    log(f"placement: point={res.point} clearance={res.clearance_px:.0f}px "
        f"ok={res.ok} ({res.reason})")
    _save_debug(res.debug, "placement", log)
    if res.point is None or not res.ok:
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

    # 3b. Zoom in at the placement point so a tiny text (large part) becomes big
    #     enough to click reliably. Cursor-anchored, so it stays under (px,py).
    _zoom_at(px, py, SELECT_ZOOM_STEPS, SELECT_SCROLL)
    log("zoomed in for selection")

    # 4. Select the placed text. Boost ignores a click on the exact same pixel
    #    just used to place, so nudge a few px -- still on the (now larger) text.
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

    # 5b. Deselect the text FIRST: Esc out of Properties, then click empty
    #     part-body. Esc alone leaves the text selected (so '2' would act on it,
    #     not save), and this click also focuses the canvas so the next
    #     Zoom-Extents hotkey fires. The click is within the placement clearance
    #     (guaranteed on the part, clear of edges/holes) and offset vertically
    #     off the (horizontal) text.
    pyautogui.press("esc")
    time.sleep(t.after_esc)
    off = max(25, min(60, int(res.clearance_px) - 10))
    dead_x, dead_y = px, py + off
    pyautogui.click(dead_x, dead_y)
    time.sleep(0.4)
    log(f"deselected text (click dead-space at {dead_x},{dead_y})")

    # 5c. Zoom back to Extents so the save + verify match the clean frame.
    pyautogui.press("z")
    time.sleep(t.after_zoom)
    log("restored zoom extents")

    # 6. Save + verify (optional).
    if do_save:
        pyautogui.press("2")
        time.sleep(t.after_save)
        post = _shot_bgr()
        log("saved (2)")
        # Verify against the CLEAN pre-placement frame: the marking is the diff
        # (the text is already full-size before save, so a pre/post-save diff
        # sees no change -- that was the false FAIL). Gated to the placement
        # point: UI chrome re-rendering elsewhere (tab-bar modified marker,
        # icon strip, frame lines) failed five good parts in the 0.7.11 run.
        v = verify_placement(clean, post, DEFAULT, rect,
                             expect_point=res.point,
                             expect_half=res.half_extent)
        log(f"verify -> {'PASS' if v.ok else 'FAIL'} ({v.reason})")
        if not v.ok:
            _save_debug(v.debug, "verify_fail", log)
            # A hard FAIL means the saved marking is outside the part body (the
            # number landed in the void beside it). Do NOT let this part go on to
            # be cut. Close Design back to Home and report failure so the runner
            # flags it and skips the cut; the operator removes the stray marking
            # and re-runs. (Verify is otherwise advisory -- a real out-of-body
            # marking is the one case that must gate.)
            if do_close:
                pyautogui.press("3")
                time.sleep(t.after_close)
                log("closed Design view (3)")
            log("part cycle FAILED verify -- marking outside body; part flagged")
            return False

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
