"""Safe part-number placement via distance transform.

The old BoostPY heuristic took the centroid of the largest bright contour. That
is wrong for three reasons: (1) it cannot tell the part interior from a hole or
from empty background (all are bright), (2) a centroid is not the safest point --
a long thin part has its centroid far from any wide clearance, and (3) it
reserves no room for the ~3x text expansion on save.

This module instead computes the *pole of inaccessibility*: the point inside the
part body (holes excluded) that is farthest from any edge. Its distance to the
nearest edge IS the available clearance, so we can compare it against the space
the expanded text needs and reject a part when there is genuinely nowhere safe.

Pipeline (all on the Design-View canvas, after Zoom Extents):
    1. Crop UI chrome to the drawing canvas.
    2. Background-difference threshold -> geometry outline. Uses |pixel - bg| so
       it works on a dark or light canvas AND rejects the faint square grid that
       Boost draws behind the part (a plain edge detector latches onto it).
    3. Morphological close so the outline forms watertight barriers.
    4. Label the free (non-outline) regions; the part body is the largest region
       that does NOT touch the border (exterior touches the border; each hole is
       its own smaller enclosed region).
    5. Erode slightly for line thickness, distance-transform, take the max.

Run standalone against a saved screenshot to see the choice and a debug overlay:

    python -m autoboost.vision.placement shot.png
    python -m autoboost.vision.placement shot.png --region 380 90 1900 1030

It prints the chosen point + clearance and writes shot.placement.png. That makes
placement tunable from screenshots alone, with no live Boost session.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import Config, DEFAULT


@dataclass
class PlacementResult:
    """Outcome of a placement search.

    point:        (x, y) in FULL-screen pixel coordinates, or None if no body found.
    clearance_px: distance from `point` to the nearest edge/hole (safety radius).
    ok:           clearance_px >= required_clearance_px.
    reason:       human-readable explanation, for logging.
    debug:        BGR overlay image (canvas-cropped) for inspection.
    """

    point: tuple[int, int] | None
    clearance_px: float
    ok: bool
    reason: str
    debug: np.ndarray | None = None


def _valid_body_mask(canvas_bgr: np.ndarray, cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    """Return (body_mask, outline_mask) for the part in a canvas crop.

    body_mask is 255 where it is safe to consider placing (inside the outer
    boundary, outside any hole); outline_mask is the closed geometry edges.

    In a line drawing the part interior is the same colour as the background and
    as the holes -- the only thing separating them is the geometry outline. So we
    treat the outline as barriers, label the enclosed free regions, and take the
    part body to be the largest enclosed region that does NOT touch the image
    border. The exterior touches the border (excluded); each hole is its own
    smaller enclosed region (excluded); what remains is the part material.
    """
    pc = cfg.placement
    gray = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2GRAY)

    # Estimate the canvas background brightness from the border strips (the part
    # is centred after Zoom Extents, so the outer frame is background).
    b = max(4, gray.shape[0] // 50)
    border_px = np.concatenate([
        gray[:b, :].ravel(), gray[-b:, :].ravel(),
        gray[:, :b].ravel(), gray[:, -b:].ravel(),
    ])
    background = int(np.median(border_px))

    # Geometry = pixels that differ strongly from the background. The faint grid
    # differs only slightly and is rejected; the dark part lines survive.
    diff = cv2.absdiff(gray, np.full_like(gray, background))
    outline = np.where(diff >= pc.geometry_delta, 255, 0).astype(np.uint8)

    # Thicken/close the outline so it forms watertight barriers with no leaks
    # between the interior and the exterior.
    k = np.ones((pc.close_kernel, pc.close_kernel), np.uint8)
    outline = cv2.dilate(outline, k, iterations=pc.close_iterations)
    outline = cv2.morphologyEx(outline, cv2.MORPH_CLOSE, k, iterations=pc.close_iterations)

    body = np.zeros(gray.shape, np.uint8)

    # Free space = everything that is not an outline pixel. connectedComponents
    # reserves label 0 for the zero pixels (the outline), and labels each
    # separated free region 1..N-1.
    free = np.where(outline > 0, 0, 255).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=8)
    if num <= 1:
        return body, outline

    # Any label appearing on the image border is exterior background.
    border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])

    best_lbl, best_area = -1, 0
    for lbl in range(1, num):
        if lbl in border:
            continue
        area = int(stats[lbl, cv2.CC_STAT_AREA])
        if area > best_area:
            best_lbl, best_area = lbl, area

    if best_lbl < 0 or best_area < pc.min_contour_area:
        return body, outline

    body[labels == best_lbl] = 255

    # Erode a little for line thickness / render blur so we never sit on an edge.
    if pc.body_erode_px > 0:
        ek = np.ones((pc.body_erode_px, pc.body_erode_px), np.uint8)
        body = cv2.erode(body, ek)

    return body, outline


def body_mask(canvas_bgr: np.ndarray, cfg: Config = DEFAULT) -> np.ndarray:
    """Public accessor for the valid-body mask of a canvas crop (holes excluded).

    Shared with vision/verify.py so both use identical segmentation.
    """
    body, _ = _valid_body_mask(canvas_bgr, cfg)
    return body


def find_safe_placement(
    screen_bgr: np.ndarray,
    cfg: Config = DEFAULT,
    canvas_rect: tuple[int, int, int, int] | None = None,
) -> PlacementResult:
    """Find the safest part-number placement point in a full-screen screenshot.

    canvas_rect (x1, y1, x2, y2) overrides the configured canvas crop; pass one
    when the input is already a canvas crop by giving the full image bounds.
    """
    h, w = screen_bgr.shape[:2]
    if canvas_rect is None:
        x1, y1, x2, y2 = cfg.canvas.to_pixels(w, h)
    else:
        x1, y1, x2, y2 = canvas_rect
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)

    canvas = screen_bgr[y1:y2, x1:x2]
    if canvas.size == 0:
        return PlacementResult(None, 0.0, False, "empty canvas region")

    body, outline = _valid_body_mask(canvas, cfg)
    debug = canvas.copy()
    # Tint the valid body green so the mask is visible in the overlay.
    debug[body > 0] = (0.6 * debug[body > 0] + np.array([0, 90, 0])).astype(np.uint8)

    if cv2.countNonZero(body) == 0:
        return PlacementResult(None, 0.0, False, "no part body detected", debug)

    # Distance transform: each body pixel -> distance to nearest non-body pixel.
    dist = cv2.distanceTransform(body, cv2.DIST_L2, 5)
    _, max_val, _, max_loc = cv2.minMaxLoc(dist)
    clearance = float(max_val)
    cx, cy = max_loc  # canvas-local

    required = cfg.placement.required_clearance_px
    ok = clearance >= required
    reason = (
        f"clearance {clearance:.1f}px "
        f"{'>=' if ok else '<'} required {required}px"
    )

    # Overlay: clearance circle + chosen point.
    colour = (0, 200, 0) if ok else (0, 0, 255)
    cv2.circle(debug, (cx, cy), int(clearance), colour, 2)
    cv2.circle(debug, (cx, cy), 4, (0, 0, 255), -1)

    point = (x1 + cx, y1 + cy)  # back to full-screen coords
    return PlacementResult(point, clearance, ok, reason, debug)


def _main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    path = argv[0]
    canvas_rect = None
    if "--region" in argv:
        i = argv.index("--region")
        canvas_rect = tuple(int(v) for v in argv[i + 1 : i + 5])

    img = cv2.imread(path)
    if img is None:
        print(f"Could not read image: {path}")
        return 1

    result = find_safe_placement(img, DEFAULT, canvas_rect)
    print(f"point (full-screen): {result.point}")
    print(f"clearance_px       : {result.clearance_px:.1f}")
    print(f"ok                 : {result.ok}")
    print(f"reason             : {result.reason}")

    if result.debug is not None:
        out = path.rsplit(".", 1)[0] + ".placement.png"
        cv2.imwrite(out, result.debug)
        print(f"overlay written    : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
