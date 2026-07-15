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
    5. Reserve the text footprint. The saved engraving is a WIDE, SHORT strip, so
       when the part's real dimensions and the part number are known the search
       reserves a RECTANGLE of that footprint (sized from the on-screen scale) and
       takes the roomiest spot where it fits clear; otherwise it falls back to the
       isotropic circle (distance-transform max) against required_clearance_px.

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
    clearance_px: isotropic clearance at `point` (distance to nearest edge/hole).
    ok:           the reserved footprint fits clear (rectangle) or clearance meets
                  the required radius (circle fallback).
    reason:       human-readable explanation, for logging.
    debug:        BGR overlay image (canvas-cropped) for inspection.
    half_extent:  (hx, hy) px half-width/half-height of the reserved text rectangle
                  when sized from the part dimensions; None for the circle fallback.
    """

    point: tuple[int, int] | None
    clearance_px: float
    ok: bool
    reason: str
    debug: np.ndarray | None = None
    half_extent: tuple[int, int] | None = None


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

    Boost draws light-grey boundary rectangles AROUND the part in Design view
    (drawing boundary, and on some parts an annotation plane too -- nested). Each
    one inserts an empty nesting level, shifting the material parity; with two of
    them the depth-offset heuristic maxes out and the body comes out inverted
    (ring + cutouts instead of material -- how 8576131EA2-1D got its number
    stencilled inside a window). Since part geometry is near-black and those
    boundaries are grey, the primary defence is the STRICT threshold pass, which
    keeps them out of the outline entirely. For genuinely dark enclosing contours
    (legacy-threshold fallback, DWG junk) a single parity shift is still detected
    two independent ways (either suffices): a depth-3 region whose depth-2
    neighbour is big (a hole INSIDE material that is itself enclosed), or the
    outermost outline enclosing another outline of comparable filled size (covers
    a part with no holes at all).

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

    # How far each pixel's brightness sits from the background. The faint grid
    # differs only slightly and is rejected; the dark part lines survive.
    diff = cv2.absdiff(gray, np.full_like(gray, background))

    # Colour saturation. Boost's CAD origin markers (red X-axis line, green
    # Y-axis line, blue 0,0 dot) are vividly coloured, and the axis lines ride
    # exactly along the part's bottom/left edges; geometry and the grey boundary
    # rects are colourless. Saturated pixels are ALWAYS barriers -- an axis line
    # that overdraws a part edge must keep acting as that edge at every
    # threshold, or the body springs a leak right where the edge should be.
    channel_max = canvas_bgr.max(axis=2).astype(np.int16)
    channel_min = canvas_bgr.min(axis=2).astype(np.int16)
    coloured = np.where(channel_max - channel_min >= pc.axis_saturation_min,
                        255, 0).astype(np.uint8)

    outline = np.zeros(gray.shape, np.uint8)
    body = np.zeros(gray.shape, np.uint8)
    k = np.ones((pc.close_kernel, pc.close_kernel), np.uint8)

    # Attempt ladder; first threshold+morphology that yields a body wins.
    # Threshold: STRICT first -- part geometry is near-black, while the grey
    # boundary rects Boost draws around the part (sometimes two, nested:
    # drawing boundary + annotation plane) must not enter the outline, or they
    # wall off phantom rings that capture the placement or invert the body
    # parity. Legacy threshold second, for a machine that renders geometry
    # lighter. Morphology: gentle first -- heavy thickening welds hole outlines
    # across thin material strips and corrupts the nesting depths -- with a
    # heavy retry only when the gentle outline leaks (no body found at all).
    deltas: list[int] = []
    for d in (pc.part_line_delta, pc.geometry_delta):
        if d not in deltas:
            deltas.append(d)
    for delta in deltas:
        raw = np.where(diff >= delta, 255, 0).astype(np.uint8)
        raw = cv2.bitwise_or(raw, coloured)
        for dilate_it, close_it in ((pc.dilate_iterations, pc.close_iterations), (2, 2)):
            outline = cv2.dilate(raw, k, iterations=dilate_it)
            outline = cv2.morphologyEx(outline, cv2.MORPH_CLOSE, k, iterations=close_it)
            body = _body_from_outline(outline, pc)
            if cv2.countNonZero(body) > 0:
                break
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


def _text_rect_px(body: np.ndarray,
                  part_dims_mm: tuple[float, float] | None,
                  char_count: int | None,
                  cfg: Config) -> tuple[int, int] | None:
    """Half-width/half-height (px) of the rectangle to reserve for the saved
    part-number engraving, or None if it can't be sized (missing inputs).

    The engraving is char_count glyphs of a font_height_mm-tall font, each glyph
    advancing char_advance_ratio of its height, plus a margin each side. The
    millimetre footprint is converted to pixels with the on-screen scale of the
    part: its body bounding box in px against its real dimensions, per axis (so a
    stretched aspect or non-square pixels are handled directionally).
    """
    if not part_dims_mm or not char_count or char_count <= 0:
        return None
    pw_mm, ph_mm = part_dims_mm
    if pw_mm <= 0 or ph_mm <= 0:
        return None
    ys, xs = np.where(body > 0)
    if xs.size == 0:
        return None
    bbox_w = int(xs.max() - xs.min()) + 1
    bbox_h = int(ys.max() - ys.min()) + 1
    if bbox_w <= 0 or bbox_h <= 0:
        return None

    pc = cfg.placement
    ppm_x = bbox_w / pw_mm      # on-screen px per mm, each axis
    ppm_y = bbox_h / ph_mm
    text_w_mm = char_count * pc.char_advance_ratio * pc.font_height_mm
    text_h_mm = pc.font_height_mm
    margin_mm = pc.text_margin_frac * pc.font_height_mm
    hx = int(round(0.5 * (text_w_mm + 2 * margin_mm) * ppm_x))
    hy = int(round(0.5 * (text_h_mm + 2 * margin_mm) * ppm_y))
    return max(1, hx), max(1, hy)


def find_safe_placement(
    screen_bgr: np.ndarray,
    cfg: Config = DEFAULT,
    canvas_rect: tuple[int, int, int, int] | None = None,
    part_dims_mm: tuple[float, float] | None = None,
    char_count: int | None = None,
) -> PlacementResult:
    """Find the safest part-number placement point in a full-screen screenshot.

    canvas_rect (x1, y1, x2, y2) overrides the configured canvas crop; pass one
    when the input is already a canvas crop by giving the full image bounds.

    part_dims_mm (width, height) and char_count size a RECTANGULAR reserved
    footprint matching the wide/short engraving; when either is absent the search
    falls back to the isotropic-circle clearance.
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
    # Used to choose the most generous spot in either mode.
    dist = cv2.distanceTransform(body, cv2.DIST_L2, 5)

    rect = _text_rect_px(body, part_dims_mm, char_count, cfg)
    if rect is not None:
        hx, hy = rect
        # A point is a valid centre iff the whole hx-by-hy rectangle is inside the
        # body: erode the body by that rectangle and any surviving pixel qualifies.
        se = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * hx + 1, 2 * hy + 1))
        valid = cv2.erode(body, se)
        if cv2.countNonZero(valid) > 0:
            # Among the spots where the text fits, take the one with the most
            # isotropic breathing room (keeps it away from the nearest edge).
            masked = dist.copy()
            masked[valid == 0] = 0
            _, _, _, max_loc = cv2.minMaxLoc(masked)
            cx, cy = max_loc
            clearance = float(dist[cy, cx])
            ok = True
            reason = (f"text {2 * hx}x{2 * hy}px fits; "
                      f"clearance {clearance:.0f}px at the chosen point")
        else:
            # The footprint doesn't fit anywhere -- report the roomiest point and
            # abort so the part is flagged rather than stamped too tight.
            _, max_val, _, max_loc = cv2.minMaxLoc(dist)
            cx, cy = max_loc
            clearance = float(max_val)
            ok = False
            reason = (f"text {2 * hx}x{2 * hy}px does not fit anywhere "
                      f"(roomiest point only {clearance:.0f}px half-clearance)")

        colour = (0, 200, 0) if ok else (0, 0, 255)
        cv2.rectangle(debug, (cx - hx, cy - hy), (cx + hx, cy + hy), colour, 2)
        cv2.circle(debug, (cx, cy), 4, (0, 0, 255), -1)
        point = (x1 + cx, y1 + cy)
        return PlacementResult(point, clearance, ok, reason, debug, (hx, hy))

    # Fallback: isotropic circle when we can't size the text footprint.
    _, max_val, _, max_loc = cv2.minMaxLoc(dist)
    clearance = float(max_val)
    cx, cy = max_loc  # canvas-local

    required = cfg.placement.required_clearance_px
    ok = clearance >= required
    reason = (
        f"clearance {clearance:.1f}px "
        f"{'>=' if ok else '<'} required {required}px (no part dims -- circle)"
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
