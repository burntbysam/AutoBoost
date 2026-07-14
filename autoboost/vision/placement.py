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
    3. Gentle thicken/close so the outline forms watertight barriers. GENTLE is
       load-bearing: hole/window outlines that sit in a thin material strip (hex
       holes in a 30px border) must not weld to the neighbouring part edge, or
       the topology below turns to garbage (that welding is what stamped
       8576131EA2-1C's number inside a window cutout). If the light pass finds
       no body at all (a leaky outline), it retries once with heavy morphology.
    4. Label the free (non-outline) regions and rank them by NESTING DEPTH (outline
       bands from the exterior). Material and empty space alternate with depth, so
       the body is the union of the solid depths (part material), which excludes
       the exterior, the holes/large window cutouts, AND the void between the part
       and the sheet/drawing boundary -- the rectangle Boost draws around the part
       in Design view -- that used to capture the placement on narrow parts.
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


def _outline_thickness(outline: np.ndarray) -> int:
    """Median width (px) of the outline strokes, used to size the reach that
    decides which free regions are separated by a *single* outline band."""
    widths: list[int] = []
    h = outline.shape[0]
    for y in range(0, h, max(1, h // 40)):
        run = 0
        for v in outline[y] > 0:
            if v:
                run += 1
            elif run:
                widths.append(run)
                run = 0
    return int(np.median(widths)) if widths else 6


def _region_depths(
    num: int, labels: np.ndarray, border: set[int], outline: np.ndarray
) -> tuple[dict[int, int], dict[int, set[int]]]:
    """Nesting depth of each free region = how many outline bands separate it from
    the exterior, plus the region-adjacency graph it was derived from. Built by a
    breadth-first walk (two regions are adjacent when a single outline band lies
    between them), so it is immune to holes/features that happen to sit along any
    straight sight-line.

        exterior 0  ->  (sheet gap) 1  ->  part material 2  ->  window/hole 3 ...
    """
    from collections import deque

    reach = _outline_thickness(outline) + 3
    disk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * reach + 1, 2 * reach + 1))

    adjacency: dict[int, set[int]] = {lbl: set() for lbl in range(1, num)}
    for lbl in range(1, num):
        grown = cv2.dilate((labels == lbl).astype(np.uint8), disk)
        neighbours = labels[(grown > 0) & (labels > 0) & (labels != lbl)]
        for other in np.unique(neighbours):
            adjacency[lbl].add(int(other))
            adjacency[int(other)].add(lbl)

    depth = {lbl: 0 for lbl in border}
    queue = deque(border)
    seen = set(border)
    while queue:
        u = queue.popleft()
        for v in adjacency.get(u, ()):
            if v not in seen:
                seen.add(v)
                depth[v] = depth[u] + 1
                queue.append(v)
    for lbl in range(1, num):
        depth.setdefault(lbl, 0)
    return depth, adjacency


def _cc_fill_area(mask: np.ndarray) -> int:
    """Area of a connected component once its enclosed interior is filled in.

    Pads by one pixel so a component touching the image edge still floods
    correctly, then floods the background from a corner; what the flood cannot
    reach is enclosed by the component.
    """
    padded = cv2.copyMakeBorder(mask, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flood = padded.copy()
    ph, pw = padded.shape[:2]
    ff_mask = np.zeros((ph + 2, pw + 2), np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    filled = cv2.bitwise_or(padded, cv2.bitwise_not(flood))
    return int(cv2.countNonZero(filled))


def _sheet_boundary_present(outline: np.ndarray, min_area: int) -> bool:
    """Is the outermost outline a sheet/drawing boundary AROUND the part, rather
    than the part itself?

    Boost draws a thin rectangle around the part in Design view (the drawing /
    raw-material boundary). Its signature: the outline component with the largest
    filled interior encloses ANOTHER outline component whose own filled interior
    is comparable (the part). A plain part encloses only its small holes, so the
    ratio stays tiny; window cutouts stay well under the threshold because a
    window is a fraction of its part. Detected by comparing the two largest
    filled-interior areas among the outline's connected components.
    """
    num, labels, stats, _ = cv2.connectedComponentsWithStats(outline, connectivity=8)
    fills: list[int] = []
    for lbl in range(1, num):
        x, y, bw, bh, area = stats[lbl]
        if bw * bh < min_area:      # bbox bounds the fill; skip specks cheaply
            continue
        fills.append(_cc_fill_area((labels == lbl).astype(np.uint8) * 255))
    if len(fills) < 2:
        return False
    fills.sort(reverse=True)
    return fills[1] >= 0.6 * fills[0]


def _valid_body_mask(canvas_bgr: np.ndarray, cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    """Return (body_mask, outline_mask) for the part in a canvas crop.

    body_mask is 255 where it is safe to consider placing (inside the outer
    boundary, outside any hole); outline_mask is the closed geometry edges.

    In a line drawing the part interior is the same colour as the background and
    as the holes -- the only thing separating them is the geometry outline. So we
    treat the outline as barriers, label the enclosed free regions, and classify
    each by its NESTING DEPTH (outline bands from the exterior). Material and empty
    space alternate with depth, so the body is the union of the "solid" depths:

        exterior 0  ->  part material 1  ->  hole/window 2  ->  island 3 ...

    Boost draws a sheet/drawing boundary rectangle AROUND the part in Design view.
    It inserts one extra empty level -- the gap between the boundary and the part --
    shifting the material to depth 2. That gap can be the single largest enclosed
    region (a narrow part on a wide sheet), and the old "largest enclosed region"
    rule planted the part-number in it, OUTSIDE the part. The parity shift is
    detected two independent ways (either suffices): a depth-3 region whose
    depth-2 neighbour is big (a hole INSIDE material that is itself enclosed), or
    the outermost outline enclosing another outline of comparable filled size
    (the boundary around the part -- catches a part with no holes at all).

    Morphology is gentle-first (thin material strips must not weld shut -- see the
    module docstring) with one heavy retry if the gentle outline leaks.
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
    raw = np.where(diff >= pc.geometry_delta, 255, 0).astype(np.uint8)

    outline = raw
    body = np.zeros(gray.shape, np.uint8)
    k = np.ones((pc.close_kernel, pc.close_kernel), np.uint8)

    # Gentle first: heavy thickening welds hole outlines to the part edge across
    # thin material strips and corrupts the nesting depths. Escalate to heavy
    # only when the gentle outline leaks (no body found at all).
    attempts = ((pc.dilate_iterations, pc.close_iterations), (2, 2))
    for dilate_it, close_it in attempts:
        outline = cv2.dilate(raw, k, iterations=dilate_it)
        outline = cv2.morphologyEx(outline, cv2.MORPH_CLOSE, k, iterations=close_it)
        body = _body_from_outline(outline, pc)
        if cv2.countNonZero(body) > 0:
            break

    # Erode a little for line thickness / render blur so we never sit on an edge.
    if pc.body_erode_px > 0 and cv2.countNonZero(body) > 0:
        ek = np.ones((pc.body_erode_px, pc.body_erode_px), np.uint8)
        body = cv2.erode(body, ek)

    return body, outline


def _body_from_outline(outline: np.ndarray, pc) -> np.ndarray:
    """Material mask for one watertight outline: label free regions, rank by
    nesting depth, take the union of the solid-parity depths."""
    body = np.zeros(outline.shape, np.uint8)

    # Free space = everything that is not an outline pixel. connectedComponents
    # reserves label 0 for the zero pixels (the outline), and labels each
    # separated free region 1..N-1.
    free = np.where(outline > 0, 0, 255).astype(np.uint8)
    num, labels, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=8)
    if num <= 1:
        return body

    # Any label appearing on the image border is exterior background.
    border = set(labels[0, :]) | set(labels[-1, :]) | set(labels[:, 0]) | set(labels[:, -1])

    depth, adjacency = _region_depths(num, labels, border, outline)

    # Does a sheet/drawing boundary shift the material parity by one? Two
    # independent signatures, either suffices:
    #   (a) a hole inside enclosed material: some depth-3 region whose depth-2
    #       neighbour is substantial. Requiring the BIG depth-2 parent keeps a
    #       tiny pocket inside an icon or text glyph from faking the signal.
    #   (b) the outermost outline encloses another outline of comparable filled
    #       size (the boundary around the part) -- catches a part with no holes.
    def _deep_hole_witness() -> bool:
        for lbl in range(1, num):
            if lbl in border or depth[lbl] != 3:
                continue
            for nb in adjacency.get(lbl, ()):
                if depth.get(nb) == 2 and int(stats[nb, cv2.CC_STAT_AREA]) >= pc.min_contour_area:
                    return True
        return False

    sheet_offset = 1 if (_deep_hole_witness()
                         or _sheet_boundary_present(outline, pc.min_contour_area)) else 0

    # Union every material region (a part can have several disjoint solid areas,
    # e.g. strips separated by large window cutouts). The distance transform later
    # picks the safest single point across all of them.
    for lbl in range(1, num):
        if lbl in border:
            continue
        d = depth[lbl] - sheet_offset
        if d >= 1 and d % 2 == 1 and int(stats[lbl, cv2.CC_STAT_AREA]) >= pc.min_contour_area:
            body[labels == lbl] = 255

    return body


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
