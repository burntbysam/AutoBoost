"""Regression tests for the two real mis-placement bugs (0.7.7 / 0.7.8).

Two parts stencilled their number in the wrong place:

  - 8604300I-1: a narrow part sitting at the left of Boost's sheet/drawing
    boundary rectangle. The void between the boundary and the part was the
    largest enclosed region, so the old "largest enclosed region" rule put the
    number in the void, right of the part.
  - 8576131EA2-1C: a wide part with two large window cutouts and hex holes in
    ~34px material strips. Heavy line-thickening welded the hole outlines to the
    part/window edges, corrupting the region topology, and the number landed
    inside a window cutout. The 20%-left crop fraction also sliced through the
    sheet boundary and the part's left edge.

These tests reproduce both geometries (including a faithful full-screenshot
replica of 8576131EA2-1C) and lock in the fixes:

  - placement lands ON MATERIAL: never the sheet void, never a window cutout,
  - a plain part is unaffected,
  - verify FAILs a marking sitting out in the void but still assumes-clear a
    thin antialiased sliver at the body edge.

Runnable with pytest or directly:  python -m tests.test_placement_frame
"""

from __future__ import annotations

import numpy as np
import cv2

from autoboost.config import DEFAULT
from autoboost.vision.placement import find_safe_placement, body_mask
from autoboost.vision.verify import verify_placement


W, H = 1610, 845
DARK = (30, 30, 30)


def _canvas(part_box, frame_box=None, windows=()):
    """Light canvas with a dark part-outline rectangle (+ top/bottom hole rows),
    optionally a sheet-border frame around it, and optionally large rectangular
    window cutouts inside it."""
    img = np.full((H, W, 3), 245, np.uint8)
    if frame_box:
        cv2.rectangle(img, frame_box[:2], frame_box[2:], DARK, 2)
    l, t, r, b = part_box
    cv2.rectangle(img, (l, t), (r, b), DARK, 2)
    for win in windows:
        cv2.rectangle(img, win[:2], win[2:], DARK, 2)
    for i in range(6):
        cx = int(l + (i + 1) * (r - l) / 7)
        cv2.circle(img, (cx, t + 30), 11, DARK, 2)
        cv2.circle(img, (cx, b - 30), 11, DARK, 2)
    return img


def _place(part_box, frame_box=None, windows=()):
    img = _canvas(part_box, frame_box, windows)
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H))
    return img, res


def _inside(pt, box):
    l, t, r, b = box
    return pt is not None and l < pt[0] < r and t < pt[1] < b


def test_plain_part_places_inside():
    part = (590, 25, 1020, 820)          # narrow, tall, centred
    _, res = _place(part)
    assert _inside(res.point, part), f"plain part placed outside: {res.point}"


def test_framed_part_left_places_inside():
    # Part on the LEFT with a border frame -> void on the right. This is the
    # 8604300I-1 case: the old code placed the number to the right of the part.
    part = (120, 60, 470, 790)
    frame = (40, 30, 1570, 815)
    _, res = _place(part, frame)
    assert _inside(res.point, part), \
        f"framed part-left placed outside part (in the void): {res.point}"


def test_framed_part_centered_places_inside():
    part = (980, 60, 1330, 790)
    frame = (40, 30, 1570, 815)
    _, res = _place(part, frame)
    assert _inside(res.point, part), f"framed part placed outside: {res.point}"


def test_framed_part_with_windows_places_on_material():
    # 8576131EA2-1C: a sheet frame AND two large rectangular window cutouts. The
    # number must land on part material -- not in the frame void, not in a window.
    part = (70, 110, 1540, 735)
    frame = (30, 40, 1580, 800)
    win_l = (170, 180, 760, 660)
    win_r = (830, 180, 1470, 660)
    _, res = _place(part, frame, windows=(win_l, win_r))
    assert _inside(res.point, part), f"placed outside part: {res.point}"
    assert not _inside(res.point, win_l) and not _inside(res.point, win_r), \
        f"placed inside a window cutout: {res.point}"


def test_windows_no_frame_places_on_material():
    # Same big cutouts but no frame -- the body is still the material, never a
    # window, even though a window can be larger than a material strip.
    part = (120, 80, 1490, 760)
    win_l = (200, 160, 720, 680)
    win_r = (890, 160, 1410, 680)
    _, res = _place(part, windows=(win_l, win_r))
    assert _inside(res.point, part), f"placed outside part: {res.point}"
    assert not _inside(res.point, win_l) and not _inside(res.point, win_r), \
        f"placed inside a window cutout: {res.point}"


def test_big_part_filling_view_not_treated_as_frame():
    # A genuinely large part that fills most of the view has no second contour
    # inside it, so it must NOT be stripped as a frame.
    part = (60, 40, 1550, 805)
    _, res = _place(part)
    assert _inside(res.point, part), f"big part wrongly stripped: {res.point}"


def _hexagon(img, cx, cy, r, color, th=1):
    pts = []
    for k in range(6):
        a = np.pi / 3 * k + np.pi / 6
        pts.append([int(cx + r * np.cos(a)), int(cy + r * np.sin(a))])
    cv2.polylines(img, [np.array(pts)], True, color, th)


GREY = (150, 150, 150)     # Boost's boundary rects: visible at the legacy
                           # threshold (diff 95 >= 80) but below the strict one


def _replica_8576131(double_boundary=False):
    """Faithful full-screenshot replica of the real 8576131EA2-1C Design view
    (1920x1080), coordinates measured from the workstation screenshot: grey
    boundary rect(s), part outline, two big windows, hex holes centred in ~34px
    strips, faint canvas grid, and the red/green axis lines. Run through the
    SAME path as `py -m autoboost.vision.placement shot.png` (fraction crop).

    double_boundary adds the second nested grey rectangle (annotation plane)
    that 8576131EA2-1D carries -- the extra nesting level that inverted the
    body parity and put its number inside a window cutout."""
    img = np.full((1080, 1920, 3), 245, np.uint8)
    for x in range(0, 1920, 17):
        img[:, x] = (233, 233, 233)
    for y in range(0, 1080, 17):
        img[y, :] = (233, 233, 233)
    cv2.rectangle(img, (352, 315), (1865, 830), (120, 120, 120), 1)   # drawing boundary
    if double_boundary:
        cv2.rectangle(img, (340, 303), (1877, 842), GREY, 1)          # annotation plane
    cv2.rectangle(img, (383, 347), (1833, 797), DARK, 1)              # part outline
    cv2.rectangle(img, (437, 381), (806, 764), DARK, 1)               # left window
    cv2.rectangle(img, (917, 381), (1770, 764), DARK, 1)              # right window
    for x in [420, 622, 824, 900, 1116, 1344, 1573, 1789]:
        _hexagon(img, x, 364, 8, DARK)
        _hexagon(img, x, 780, 8, DARK)
    for x in [420, 824, 900, 1789]:
        _hexagon(img, x, 572, 8, DARK)
    img[798:800, :] = (0, 0, 255)              # red X axis on the part bottom edge
    img[650:920, 384:386] = (0, 200, 0)        # green Y axis stub
    return img


def test_replica_8576131_places_on_material():
    img = _replica_8576131()
    res = find_safe_placement(img, DEFAULT)    # fraction crop, like the CLI
    part = (383, 347, 1833, 797)
    win_l = (437, 381, 806, 764)
    win_r = (917, 381, 1770, 764)
    assert _inside(res.point, part), f"placed outside the part: {res.point}"
    assert not _inside(res.point, win_l) and not _inside(res.point, win_r), \
        f"placed inside a window cutout: {res.point}"


def test_narrow_part_on_wide_sheet_places_inside():
    """The original 8604300I-1 shape: the sheet/drawing boundary is ~2x the part
    width with the part at its left, so the right-hand void inside the boundary
    is much larger than the part interior. The number must still land on the
    part."""
    part = (410, 40, 838, 800)                 # narrow, tall, at the left
    sheet = (400, 20, 1214, 820)               # boundary ~2x the part width
    _, res = _place(part, sheet)
    assert _inside(res.point, part), \
        f"narrow part on wide sheet placed in the void: {res.point}"


def test_replica_double_boundary_places_on_material():
    """The 8576131EA2-1D failure: TWO nested grey boundary rects (drawing
    boundary + annotation plane) inverted the body parity, so the run stencilled
    the number inside the right window. With grey boundaries kept out of the
    outline the count of them must not matter."""
    img = _replica_8576131(double_boundary=True)
    res = find_safe_placement(img, DEFAULT)
    part = (383, 347, 1833, 797)
    win_l = (437, 381, 806, 764)
    win_r = (917, 381, 1770, 764)
    assert _inside(res.point, part), f"placed outside the part: {res.point}"
    assert not _inside(res.point, win_l) and not _inside(res.point, win_r), \
        f"placed inside a window cutout: {res.point}"


def test_double_grey_boundary_plain_part():
    """The 8576131EA2-09 failure: a plain part inside doubled grey boundaries
    had the thin boundary ring picked as the body (16px clearance, aborted).
    The interior must be picked instead."""
    img = np.full((H, W, 3), 245, np.uint8)
    part = (420, 60, 1190, 790)
    cv2.rectangle(img, (390, 35), (1220, 815), (120, 120, 120), 1)
    cv2.rectangle(img, (378, 24), (1232, 826), GREY, 1)
    cv2.rectangle(img, part[:2], part[2:], DARK, 1)
    for i in range(5):
        cx = int(part[0] + (i + 1) * (part[2] - part[0]) / 6)
        cv2.circle(img, (cx, part[1] + 30), 11, DARK, 2)
        cv2.circle(img, (cx, part[3] - 30), 11, DARK, 2)
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H))
    assert _inside(res.point, part), f"placed in the boundary ring: {res.point}"
    assert res.clearance_px > 100, \
        f"clearance {res.clearance_px:.0f}px says the thin ring was picked, not the interior"


def test_axis_marks_do_not_break_segmentation():
    """Boost draws the CAD origin in colour: a red X-axis line and green Y-axis
    line riding EXACTLY along the part's bottom/left edges (overdrawing them),
    plus a blue 0,0 dot. Colour-saturated pixels must act as barriers, so the
    overdrawn edges stay sealed and the body is still the interior."""
    img = np.full((H, W, 3), 245, np.uint8)
    part = (420, 60, 1190, 790)
    cv2.rectangle(img, part[:2], part[2:], DARK, 1)
    for i in range(5):
        cx = int(part[0] + (i + 1) * (part[2] - part[0]) / 6)
        cv2.circle(img, (cx, part[1] + 30), 11, DARK, 2)
        cv2.circle(img, (cx, part[3] - 30), 11, DARK, 2)
    # Axis lines OVERWRITE the part's left and bottom edges (worst case: the
    # dark line is fully occluded by the coloured one), and extend beyond.
    img[part[3] - 1:part[3] + 1, :] = (0, 0, 255)          # red X axis, full width
    img[100:H, part[0] - 1:part[0] + 1] = (0, 200, 0)      # green Y axis
    cv2.circle(img, (part[0], part[3]), 4, (255, 80, 0), -1)   # blue-ish origin dot
    # Arrowheads out in the void.
    cv2.arrowedLine(img, (part[0], part[3] + 20), (part[0] + 60, part[3] + 20),
                    (0, 0, 255), 2, tipLength=0.5)
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H))
    assert _inside(res.point, part), \
        f"axis overdraw broke the body (placed at {res.point})"
    assert res.clearance_px > 100, \
        f"clearance {res.clearance_px:.0f}px suggests a leak or sliver body"


def test_verify_detects_faint_marking_in_void():
    """The 8576131EA2-1D verify blind spot: at Zoom Extents the saved engraving
    was a faint 1px stroke, invisible at geometry_delta, so verify said 'no
    marking change detected' about a number sitting in a window cutout. The
    low-contrast rescue pass must FAIL it."""
    part = (120, 60, 470, 790)
    pre = _canvas(part)
    post = pre.copy()
    cv2.putText(post, "8576131EA2-1D", (900, 420),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (185, 185, 185), 1)
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert not v.ok, f"faint marking in the void slipped past verify: {v.reason}"


def test_verify_ignores_faint_edge_jitter():
    """Render jitter: geometry edges re-rasterise slightly darker between the
    two frames. Faint slivers hugging the body must NOT fail."""
    part = (120, 60, 470, 790)
    pre = _canvas(part)
    post = pre.copy()
    # Redraw the part outline 1px shifted, faintly darker than background.
    cv2.rectangle(post, (part[0] + 1, part[1] + 1), (part[2] + 1, part[3] + 1),
                  (200, 200, 200), 1)
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert v.ok, f"edge jitter wrongly FAILed: {v.reason}"


def test_verify_fails_marking_in_the_void():
    # Body on the left; a marking blob out in the right-hand void must FAIL.
    part = (120, 60, 470, 790)
    pre = _canvas(part)
    post = pre.copy()
    cv2.putText(post, "8604300I-1", (1100, 420),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, DARK, 2)
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert not v.ok, f"expected FAIL for marking in the void, got: {v.reason}"


def test_verify_assumes_clear_for_edge_sliver():
    # A couple of stray pixels right on the body boundary (antialiasing sliver)
    # must stay assumed-clear, not FAIL.
    part = (120, 60, 470, 790)
    body = body_mask(_canvas(part), DEFAULT)
    ys, xs = np.where(body > 0)
    # A point near the body's right edge.
    ex = int(xs.max())
    ey = int(ys[xs.argmax()])
    pre = _canvas(part)
    post = pre.copy()
    cv2.circle(post, (ex, ey), 2, DARK, -1)
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert v.ok, f"edge sliver wrongly FAILed: {v.reason}"


def test_walled_crop_edges_still_finds_part():
    """Live 8604300I-1 failure (0.7.10): faint UI junk at the crop edges walled
    three borders, the background read as 'enclosed', became the body, and the
    number was stamped in the void. With band-seeded exteriors the background
    must stay exterior no matter what hugs the border rows."""
    img = np.full((H, W, 3), 245, np.uint8)
    part = (620, 40, 990, 800)                   # narrow portrait part
    cv2.rectangle(img, part[:2], part[2:], (58, 58, 58), 1)
    for i in range(4):
        cx = int(part[0] + (i + 1) * (part[2] - part[0]) / 5)
        cv2.circle(img, (cx, part[1] + 25), 6, (58, 58, 58), 1)
        cv2.circle(img, (cx, part[3] - 25), 6, (58, 58, 58), 1)
    img[0:3, :] = (90, 90, 90)                   # hint-text row walls the top
    img[-3:, :] = (90, 90, 90)                   # icon strip walls the bottom
    img[:, -3:] = (90, 90, 90)                   # viewport line walls the right
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H))
    assert _inside(res.point, part), \
        f"walled crop edges inverted the body again: {res.point} ({res.reason})"


def test_aspect_gate_aborts_on_mismatch():
    """The real part dimensions are ground truth: if the detected body's aspect
    is wildly off the part's real aspect, segmentation grabbed something that is
    not the part -- abort instead of stamping (the 0.7.10 void stamp)."""
    part = (120, 200, 1490, 640)                 # landscape body on screen
    img = _canvas(part)
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H),
                              part_dims_mm=(592.0, 1106.0),   # claims portrait
                              char_count=10)
    assert not res.ok and "mismatch" in res.reason, \
        f"aspect mismatch should abort: {res.reason}"


def _yellow_text(img, org, scale=0.7):
    cv2.putText(img, "8604305I-1__01", org,
                cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 230, 230), 1)


def test_verify_detects_yellow_marking_on_material():
    # The engraving renders YELLOW: bright (invisible to a darkness diff) but
    # vividly saturated. On material it must be DETECTED and PASS.
    part = (120, 60, 1400, 790)
    pre = _canvas(part)
    post = pre.copy()
    _yellow_text(post, (700, 420))
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert v.ok, f"yellow marking on material should PASS: {v.reason}"
    assert v.text_px >= 20, \
        f"yellow marking should be DETECTED, not 'no change' ({v.reason})"


def test_verify_fails_yellow_marking_in_void():
    part = (120, 60, 470, 790)
    pre = _canvas(part)
    post = pre.copy()
    _yellow_text(post, (1000, 420))              # out in the void
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert not v.ok, f"yellow marking in the void must FAIL: {v.reason}"


def test_verify_ignores_shifted_axis_line():
    """Live false-FAILs (0.7.10, parts 2-5): the red/green axis lines and the
    hint-text row re-render a couple of px away between the clean and post
    frames; those long thin slivers were flagged as 'markings far outside the
    body'. Line-shaped components must be discarded."""
    part = (120, 60, 1400, 790)
    pre = _canvas(part)
    pre[794:796, :] = (0, 0, 255)                # red X axis in the clean frame
    post = _canvas(part)
    post[796:798, :] = (0, 0, 255)               # ...shifted 2px in the post frame
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert v.ok, f"shifted axis line wrongly FAILed: {v.reason}"


def _ui_junk(post):
    """The UI re-renders that failed all five parts of the live 0.7.11 run:
    the tab-bar title gaining its modified marker (compact text blobs near the
    bottom, >10px from the crop edge so the near-edge filter misses them), the
    bottom icon strip, and a viewport frame line shifted 1px (1x38 vertical --
    aspect 38, just under the MAX_ASPECT=40 filter)."""
    cv2.putText(post, "8604300I-1_01*", (45, H - 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, DARK, 1)
    cv2.rectangle(post, (660, H - 60), (676, H - 56), DARK, -1)   # icon re-render
    post[H - 90:H - 52, 1048] = DARK                              # frame line, 1x38


def test_verify_gated_ignores_ui_rerenders():
    """Live 0.7.11: markings placed correctly on all five parts, but tab-bar /
    icon-strip / frame-line re-renders far from the placement point were counted
    as collisions -> 0/17. Gated to the expected point, those diffs are ignored
    and the good marking PASSes."""
    part = (120, 60, 1400, 790)
    pre = _canvas(part)
    post = pre.copy()
    _yellow_text(post, (700, 420))
    _ui_junk(post)
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H))
    assert not v.ok, "sanity: ungated verify should still trip on the UI junk"
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H),
                         expect_point=(700, 415), expect_half=(44, 10))
    assert v.ok, f"gated verify must ignore far-away UI re-renders: {v.reason}"
    assert v.text_px >= 20, f"the real marking must still be DETECTED ({v.reason})"


def test_verify_gated_still_fails_void_stamp():
    # A void stamp appears AT the expected point (that's where we clicked), so
    # gating must not weaken the void catch.
    part = (120, 60, 470, 790)
    pre = _canvas(part)
    post = pre.copy()
    _yellow_text(post, (1000, 420))
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H),
                         expect_point=(1030, 415), expect_half=(44, 10))
    assert not v.ok, f"void stamp at the expected point must FAIL: {v.reason}"


def test_verify_gated_rescue_ignores_faint_far_junk():
    # No detectable marking (e.g. it rendered at 1px, sub-threshold), but the
    # tab-bar text changed faintly: the low-contrast rescue must not flag that
    # far-from-point junk as a "marking in the void".
    part = (120, 60, 1400, 790)
    pre = _canvas(part)
    post = pre.copy()
    cv2.putText(post, "8604300I-1_01*", (45, H - 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (185, 185, 185), 1)
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H),
                         expect_point=(700, 415), expect_half=(44, 10))
    assert v.ok, f"faint far-away UI junk wrongly FAILed the rescue pass: {v.reason}"


def test_verify_gated_rescue_still_fails_faint_void_stamp():
    # The 8576131EA2-1D blind spot, with gating: a faint stamp at the expected
    # point sitting in the void must still FAIL.
    part = (120, 60, 470, 790)
    pre = _canvas(part)
    post = pre.copy()
    cv2.putText(post, "8576131EA2-1D", (900, 420),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (185, 185, 185), 1)
    v = verify_placement(pre, post, DEFAULT, (0, 0, W, H),
                         expect_point=(985, 415), expect_half=(60, 10))
    assert not v.ok, f"faint void stamp at the expected point must FAIL: {v.reason}"


def test_text_rectangle_is_wide_and_fits_on_material():
    # A wide part (real 1000mm x 300mm) that fills a ~1450px-wide body: a 13-char
    # number reserves a WIDE, SHORT rectangle and must fit on the material.
    part = (120, 200, 1490, 640)
    img = _canvas(part)
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H),
                              part_dims_mm=(1000.0, 300.0), char_count=13)
    assert res.half_extent is not None, "expected a rectangular footprint"
    hx, hy = res.half_extent
    assert hx > hy * 2, f"footprint should be much wider than tall, got {hx}x{hy}"
    assert res.ok and _inside(res.point, part), \
        f"wide text should fit on this part: {res.reason}"


def test_text_rectangle_scales_with_zoom():
    # Same part number, but the part is reported HALF the real size -> at Zoom
    # Extents each mm is twice the pixels -> the reserved rectangle doubles.
    part = (120, 200, 1490, 640)
    img = _canvas(part)
    big = find_safe_placement(img, DEFAULT, (0, 0, W, H),
                              part_dims_mm=(500.0, 150.0), char_count=13)
    small = find_safe_placement(img, DEFAULT, (0, 0, W, H),
                                part_dims_mm=(1000.0, 300.0), char_count=13)
    assert big.half_extent[0] > 1.8 * small.half_extent[0], \
        f"halving real size should ~double the px footprint: {big.half_extent} vs {small.half_extent}"


def test_text_rectangle_aborts_when_it_cannot_fit():
    # A long number on a small part: the wide rectangle can't fit -> abort (ok
    # False) so the runner flags it instead of stamping over an edge.
    part = (700, 380, 950, 520)      # small part
    img = _canvas(part)
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H),
                              part_dims_mm=(60.0, 34.0), char_count=24)
    assert res.half_extent is not None
    assert not res.ok, f"a too-wide number should not fit: {res.reason}"


def test_no_dims_falls_back_to_circle():
    part = (120, 200, 1490, 640)
    img = _canvas(part)
    res = find_safe_placement(img, DEFAULT, (0, 0, W, H))     # no dims/char_count
    assert res.half_extent is None, "without dims it must use the circle path"
    assert _inside(res.point, part)


def test_parse_dims_mm():
    from autoboost.part_cycle import _parse_dims_mm
    w, h = _parse_dims_mm("40.3 in x 12.6 in")
    assert abs(w - 40.3 * 25.4) < 0.1 and abs(h - 12.6 * 25.4) < 0.1
    w2, h2 = _parse_dims_mm("18 mm x 9 mm")
    assert abs(w2 - 18) < 0.1 and abs(h2 - 9) < 0.1
    assert _parse_dims_mm("") is None
    assert _parse_dims_mm("garbage") is None


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
