"""Regression tests for the sheet-frame placement bug (0.7.7).

Part 8604300I-1 imported with a drawing-border outline around the sheet (Boost
flagged "several outer contours"). That border walled off the empty gap between
it and the narrow part into an enclosed region LARGER than the part interior, so
the old placement picked that void and stencilled the part-number outside the
part. These tests reproduce the geometry synthetically and lock in the fix:

  - a framed part now places INSIDE the part body,
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


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
