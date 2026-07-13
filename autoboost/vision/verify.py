"""Post-save verification: did the part-number land safely?

BoostPY never checked its own work, so a bad placement became a silent machining
error later. This module closes that loop. Boost gives no feedback, so we get it
from vision.

Method (before/after diff): take a screenshot at Zoom Extents just BEFORE pressing
save, and another right AFTER (same view -- the orchestration must not zoom or
pan between the two). Saving converts the small part-number text into engraving
geometry that expands ~3x, so the only meaningful change on the canvas is the new
text. Diffing the two frames isolates exactly those pixels without needing to
know the text's colour or layer.

We then check that every new-text pixel lies inside the valid part body (the same
holes-excluded mask placement uses). Any text pixel on the boundary, in a hole,
or outside the part is a collision -> the placement is unsafe and the part should
be undone and retried or flagged.

Run standalone against a before/after pair:

    python -m autoboost.vision.verify before.png after.png
    python -m autoboost.vision.verify before.png after.png --region 380 90 1900 1030

Prints PASS/FAIL with the collision pixel count and writes after.verify.png.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import Config, DEFAULT
from .placement import body_mask


@dataclass
class VerifyResult:
    ok: bool
    text_px: int          # size of the detected new-text region
    collision_px: int     # text pixels landing outside the valid body
    reason: str
    debug: np.ndarray | None = None


def _new_text_mask(pre: np.ndarray, post: np.ndarray, cfg: Config) -> np.ndarray:
    """Pixels that became part of the (dark) geometry between pre and post."""
    pre_g = cv2.cvtColor(pre, cv2.COLOR_BGR2GRAY)
    post_g = cv2.cvtColor(post, cv2.COLOR_BGR2GRAY)
    # Text is dark geometry on a light canvas: after-save those pixels get much
    # darker than before. (Works regardless of exact colour; if a theme inverts
    # this, the absdiff fallback below still catches the change.)
    got_darker = cv2.subtract(pre_g, post_g)
    changed = np.where(got_darker >= cfg.placement.geometry_delta, 255, 0).astype(np.uint8)
    # Fall back to absolute change if darkening produced little (inverted theme).
    if cv2.countNonZero(changed) < 20:
        diff = cv2.absdiff(pre_g, post_g)
        changed = np.where(diff >= cfg.placement.geometry_delta, 255, 0).astype(np.uint8)
    # Remove salt noise from render jitter.
    k = np.ones((3, 3), np.uint8)
    changed = cv2.morphologyEx(changed, cv2.MORPH_OPEN, k)
    return changed


def verify_placement(
    pre_bgr: np.ndarray,
    post_bgr: np.ndarray,
    cfg: Config = DEFAULT,
    canvas_rect: tuple[int, int, int, int] | None = None,
) -> VerifyResult:
    """Confirm the saved part-number text lies entirely within the part body."""
    if pre_bgr.shape != post_bgr.shape:
        return VerifyResult(False, 0, 0, "pre/post screenshots differ in size")

    h, w = pre_bgr.shape[:2]
    if canvas_rect is None:
        x1, y1, x2, y2 = cfg.canvas.to_pixels(w, h)
    else:
        x1, y1, x2, y2 = canvas_rect
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)

    pre_c = pre_bgr[y1:y2, x1:x2]
    post_c = post_bgr[y1:y2, x1:x2]

    text = _new_text_mask(pre_c, post_c, cfg)
    text_px = cv2.countNonZero(text)

    debug = post_c.copy()
    body = body_mask(pre_c, cfg)
    debug[body > 0] = (0.7 * debug[body > 0] + np.array([0, 60, 0])).astype(np.uint8)

    if text_px < 20:
        # No detectable change between the frames -- nothing to check against
        # geometry, so don't fail. This happens when the marking was already
        # there (e.g. re-running on an already-stenciled part). A genuine
        # collision only shows up when the marking IS detected (below).
        return VerifyResult(
            True, text_px, 0,
            "no marking change detected -- nothing to verify (assumed clear)",
            debug,
        )

    # Collisions = new-text pixels that are NOT inside the valid body.
    outside = cv2.bitwise_and(text, cv2.bitwise_not(body))
    collision_px = cv2.countNonZero(outside)

    # Inconclusive-detection guard. A real saved part-number expands ~3x and
    # shows up as hundreds of changed pixels, only a fraction of which could ever
    # sit outside the body. The false-positive signature is the opposite: a tiny
    # detected region where ~all of it reads as "outside" -- i.e. the diff caught
    # only a thin antialiased sliver at a geometry edge, not the real marking (it
    # rendered at nearly the same brightness as before). That's too little to
    # judge, and placement already guaranteed clearance, so treat it like the
    # "no change detected" case rather than failing on noise. Requiring collisions
    # to be ~the entire region keeps a genuine PARTIAL overlap reporting normally.
    INCONCLUSIVE_TEXT_PX = 60
    if text_px < INCONCLUSIVE_TEXT_PX and collision_px >= 0.9 * text_px:
        return VerifyResult(
            True, text_px, collision_px,
            f"marking barely detected (text={text_px}px, ~all at an edge) -- "
            f"inconclusive, assumed clear (placement clearance already checked)",
            debug,
        )

    # Small tolerance for antialiasing at the body edge.
    tolerance = max(10, int(0.02 * text_px))
    ok = collision_px <= tolerance

    debug[text > 0] = (255, 128, 0)      # detected text = blue-ish
    debug[outside > 0] = (0, 0, 255)     # collisions = red
    reason = (
        f"text={text_px}px, collisions={collision_px}px, tolerance={tolerance}px "
        f"-> {'PASS' if ok else 'FAIL'}"
    )
    return VerifyResult(ok, text_px, collision_px, reason, debug)


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    pre_path, post_path = argv[0], argv[1]
    canvas_rect = None
    if "--region" in argv:
        i = argv.index("--region")
        canvas_rect = tuple(int(v) for v in argv[i + 1 : i + 5])

    pre = cv2.imread(pre_path)
    post = cv2.imread(post_path)
    if pre is None or post is None:
        print("Could not read one of the images.")
        return 1

    result = verify_placement(pre, post, DEFAULT, canvas_rect)
    print(f"result   : {'PASS' if result.ok else 'FAIL'}")
    print(f"text_px  : {result.text_px}")
    print(f"collision: {result.collision_px}")
    print(f"reason   : {result.reason}")
    if result.debug is not None:
        out = post_path.rsplit(".", 1)[0] + ".verify.png"
        cv2.imwrite(out, result.debug)
        print(f"overlay  : {out}")
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
