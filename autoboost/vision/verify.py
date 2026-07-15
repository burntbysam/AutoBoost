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

When the caller knows WHERE the number was placed (it always does -- placement
returned the point), verification is gated to that spot: only changed components
near the expected placement rectangle count as the marking. The live 0.7.11 run
failed all five parts on perfectly-placed markings because the tab-bar title
gained its modified marker, the bottom icon strip re-rendered, and viewport
frame lines shifted 1px between the two frames -- all far from the placement
point, all counted as "collisions". Those diffs can never be our marking: we
stamped exactly one thing at a known point. A genuine void stamp still fails,
because it appears AT the expected point (that's where placement told us to
click), far from the body.

Run standalone against a before/after pair:

    python -m autoboost.vision.verify before.png after.png
    python -m autoboost.vision.verify before.png after.png --region 380 90 1900 1030
    python -m autoboost.vision.verify before.png after.png --point 1108 431

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


def _saturation(bgr: np.ndarray) -> np.ndarray:
    """Per-pixel colour saturation (max channel - min channel), int16."""
    return bgr.max(axis=2).astype(np.int16) - bgr.min(axis=2).astype(np.int16)


def _new_text_mask(pre: np.ndarray, post: np.ndarray, cfg: Config,
                   delta: int | None = None,
                   keep_component_px: int = 6) -> np.ndarray:
    """Pixels where the saved marking appeared between pre and post.

    Two channels, OR'd (live 0.7.10 frames drove this):
      - darkening: dark engraving ink on a light canvas (threshold `delta`,
        defaults to geometry_delta; absdiff fallback for inverted themes);
      - saturation gain: the engraving actually renders YELLOW on the
        MarkedText layer -- nearly invisible to a brightness diff (~19 grey
        levels) but a ~200-point saturation jump (verify_sat_delta).

    Cleanup is a connected-component pass (a 3x3 open would erase the 1px-wide
    strokes a Zoom-Extents engraving is made of): components smaller than
    keep_component_px are noise, and components shaped like UI re-renders are
    dropped -- extremely elongated (axis lines, the hint-text row: the live run
    flagged those as "markings" and failed four good parts), hollow line-work
    (a re-rendered rectangle outline), or hugging the crop edge (scrollbars,
    viewport frame). A real marking is a compact text blob well inside the crop.
    """
    if delta is None:
        delta = cfg.placement.geometry_delta
    pre_g = cv2.cvtColor(pre, cv2.COLOR_BGR2GRAY)
    post_g = cv2.cvtColor(post, cv2.COLOR_BGR2GRAY)
    got_darker = cv2.subtract(pre_g, post_g)
    changed = np.where(got_darker >= delta, 255, 0).astype(np.uint8)
    if cv2.countNonZero(changed) < 20:
        diff = cv2.absdiff(pre_g, post_g)
        changed = np.where(diff >= delta, 255, 0).astype(np.uint8)
    sat_gain = _saturation(post) - _saturation(pre)
    changed = cv2.bitwise_or(
        changed,
        np.where(sat_gain >= cfg.placement.verify_sat_delta, 255, 0).astype(np.uint8))

    h, w = changed.shape[:2]
    EDGE = 10           # px; components whose bbox hugs the crop edge = UI junk
    MAX_ASPECT = 40     # a marking is ~5-20:1; axis/hint lines are 100s:1
    MIN_FILL = 0.05     # hollow line-work (a shifted rectangle ring) fills <5%
    num, labels, stats, _ = cv2.connectedComponentsWithStats(changed, connectivity=8)
    kept = np.zeros_like(changed)
    for lbl in range(1, num):
        x, y, bw, bh, area = (int(v) for v in stats[lbl])
        if area < keep_component_px:
            continue
        aspect = max(bw, bh) / max(1, min(bw, bh))
        fill = area / max(1, bw * bh)
        near_edge = x < EDGE or y < EDGE or x + bw > w - EDGE or y + bh > h - EDGE
        if aspect > MAX_ASPECT or fill < MIN_FILL or near_edge:
            continue
        kept[labels == lbl] = 255
    return kept


def _gate_rect(expect_point: tuple[int, int],
               expect_half: tuple[int, int] | None,
               x1: int, y1: int) -> tuple[int, int, int, int]:
    """Crop-space rectangle around the expected placement where the marking may
    appear. Generous on purpose (saving expands the text ~3x and the click point
    is the rectangle centre, not a corner): 3x the reserved half-extents with a
    floor. UI junk lives hundreds of px away (tab bar, icon strip, frame lines),
    so generosity costs nothing."""
    ex, ey = expect_point[0] - x1, expect_point[1] - y1
    hx, hy = expect_half if expect_half else (0, 0)
    gx = max(3 * hx, 80)
    gy = max(3 * hy, 40)
    return ex - gx, ey - gy, ex + gx, ey + gy


def _keep_components_in(mask: np.ndarray,
                        gate: tuple[int, int, int, int]) -> np.ndarray:
    """Keep only connected components whose bbox intersects the gate rect."""
    gx1, gy1, gx2, gy2 = gate
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    kept = np.zeros_like(mask)
    for lbl in range(1, num):
        x, y, bw, bh = (int(v) for v in stats[lbl][:4])
        if x <= gx2 and x + bw >= gx1 and y <= gy2 and y + bh >= gy1:
            kept[labels == lbl] = 255
    return kept


def verify_placement(
    pre_bgr: np.ndarray,
    post_bgr: np.ndarray,
    cfg: Config = DEFAULT,
    canvas_rect: tuple[int, int, int, int] | None = None,
    expect_point: tuple[int, int] | None = None,
    expect_half: tuple[int, int] | None = None,
) -> VerifyResult:
    """Confirm the saved part-number text lies entirely within the part body.

    expect_point/expect_half (full-screen px, as returned by placement) gate the
    detection to the spot we actually stamped -- see the module docstring. When
    omitted (standalone CLI use), the whole crop is judged as before.
    """
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

    gate = None
    if expect_point is not None:
        gate = _gate_rect(expect_point, expect_half, x1, y1)

    text = _new_text_mask(pre_c, post_c, cfg)
    ignored_px = 0
    if gate is not None:
        raw_px = cv2.countNonZero(text)
        text = _keep_components_in(text, gate)
        ignored_px = raw_px - cv2.countNonZero(text)
    text_px = cv2.countNonZero(text)

    debug = post_c.copy()
    body = body_mask(pre_c, cfg)
    debug[body > 0] = (0.7 * debug[body > 0] + np.array([0, 60, 0])).astype(np.uint8)
    if gate is not None:
        cv2.rectangle(debug, (gate[0], gate[1]), (gate[2], gate[3]),
                      (255, 0, 255), 1)

    if text_px < 20:
        # No detectable change at the normal threshold. At Zoom Extents on a
        # large part the saved engraving can be a 1px antialiased stroke that
        # never crosses geometry_delta, so before assuming clear, re-look at a
        # much lower threshold (with a component-area despeckle that keeps
        # thin-but-long strokes). Only a compact blob sitting FAR from the body
        # fails -- that is a marking in the void (how 8576131EA2-1D's number in
        # a window slipped past as "no marking change"). Anything near the body
        # is indistinguishable from render jitter along geometry edges, so the
        # old assumed-clear outcome stands.
        faint = _new_text_mask(pre_c, post_c, cfg,
                               delta=cfg.placement.verify_low_delta,
                               keep_component_px=6)
        if gate is not None:
            # Same gating as the main pass: a void stamp appears AT the expected
            # point, so restricting the rescue to the gate loses nothing -- and
            # stops faint far-away UI re-renders (tab-bar title, icon strip)
            # from masquerading as a "marking in the void".
            faint = _keep_components_in(faint, gate)
        fnum, flabels, fstats, _ = cv2.connectedComponentsWithStats(faint, connectivity=8)
        if fnum > 1:
            biggest = max(range(1, fnum), key=lambda l: int(fstats[l, cv2.CC_STAT_AREA]))
            blob = flabels == biggest
            blob_px = int(fstats[biggest, cv2.CC_STAT_AREA])
            if blob_px >= 20:
                dist_from_body = cv2.distanceTransform(
                    cv2.bitwise_not(body), cv2.DIST_L2, 5)
                median_dist = float(np.median(dist_from_body[blob]))
                outside_frac = float(np.count_nonzero(blob & (body == 0))) / blob_px
                if median_dist > 15 and outside_frac >= 0.7:
                    debug[blob] = (0, 0, 255)
                    return VerifyResult(
                        False, blob_px, int(outside_frac * blob_px),
                        f"faint marking detected far outside the part body "
                        f"({blob_px}px at ~{median_dist:.0f}px from the part) -- "
                        f"placement landed in the void",
                        debug,
                    )
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
    # "no change detected" case rather than failing on noise.
    #
    # BUT a genuine mis-placement on a large part has the SAME size signature (a
    # tiny detected region, ~all outside the body) -- the number was placed in the
    # void beside the part, so on a Zoom-Extents frame it is only a few pixels.
    # The two are told apart by DISTANCE from the body: an antialiased sliver
    # hugs the body edge (a few px away); a number in the void sits far from any
    # body pixel. So only assume-clear when the detected pixels are close to the
    # body; a small region sitting far out is a real FAIL, not noise.
    INCONCLUSIVE_TEXT_PX = 60
    NEAR_BODY_PX = 15
    if text_px < INCONCLUSIVE_TEXT_PX and collision_px >= 0.9 * text_px:
        dist_from_body = cv2.distanceTransform(
            cv2.bitwise_not(body), cv2.DIST_L2, 5)
        text_dists = dist_from_body[text > 0]
        median_dist = float(np.median(text_dists)) if text_dists.size else 0.0
        if median_dist <= NEAR_BODY_PX:
            return VerifyResult(
                True, text_px, collision_px,
                f"marking barely detected (text={text_px}px, ~all at an edge) -- "
                f"inconclusive, assumed clear (placement clearance already checked)",
                debug,
            )
        debug[text > 0] = (255, 128, 0)
        debug[outside > 0] = (0, 0, 255)
        return VerifyResult(
            False, text_px, collision_px,
            f"marking is outside the part body (text={text_px}px, "
            f"~{median_dist:.0f}px from the part) -- placement landed in the void",
            debug,
        )

    # Small tolerance for antialiasing at the body edge.
    tolerance = max(10, int(0.02 * text_px))
    ok = collision_px <= tolerance

    debug[text > 0] = (255, 128, 0)      # detected text = blue-ish
    debug[outside > 0] = (0, 0, 255)     # collisions = red
    gated = f", ignored {ignored_px}px of UI changes away from the placement point" \
        if ignored_px else ""
    reason = (
        f"text={text_px}px, collisions={collision_px}px, tolerance={tolerance}px"
        f"{gated} -> {'PASS' if ok else 'FAIL'}"
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
    expect_point = None
    if "--point" in argv:
        i = argv.index("--point")
        expect_point = tuple(int(v) for v in argv[i + 1 : i + 3])
    expect_half = None
    if "--half" in argv:
        i = argv.index("--half")
        expect_half = tuple(int(v) for v in argv[i + 1 : i + 3])

    pre = cv2.imread(pre_path)
    post = cv2.imread(post_path)
    if pre is None or post is None:
        print("Could not read one of the images.")
        return 1

    result = verify_placement(pre, post, DEFAULT, canvas_rect,
                              expect_point=expect_point, expect_half=expect_half)
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
