# AutoBoost Architecture (Beta 0.7.11)

AutoBoost automates the per-part chore in TRUMPF TruTops Boost: open a part,
place its part-number as engraving text (EasyType-L=10mm), verify the placement
is safe, save, close, and move to the next part -- unattended, for a whole job,
at a target of >=95% correct.

This is the first rebuilt iteration (formerly the flat `BoostPY_*.py` script).
The goals of the rebuild are reliability and *testability without live Boost*.

## Guiding constraints

- **No automation API.** Boost exposes no public COM/SDK/macro surface for UI
  actions (re-confirmed, and TRUMPF support was contacted about the native
  auto-marking feature; we proceed as if it does not exist). So AutoBoost drives
  the GUI.
- **Runs inside RDP.** Screenshot fidelity varies with RDP quality/scaling.
  Anything that can avoid depending on pixels should. See `BOOST_SETUP.md`.
- **Corporate-managed machines.** Only per-user `pip` packages -- no drivers,
  services, or admin installs.

## Two interaction layers

The work splits cleanly into two problems with different best tools:

1. **Chrome navigation** (buttons, menus, the Properties/font dropdown chain).
   These are *widgets*, driven by **UI Automation (UIA)** via `pywinauto` --
   control identity server-side, immune to RDP blur and resolution changes,
   which kills the "clicked 'Mode' instead of 'More...'" class of failure.

   The probes (`tools/probe_uia.py`) confirmed this is viable and is the chosen
   path. Findings:
     - HomeZone (WPF): parts list, open/save/close, BOOST all have stable
       automation_ids (e.g. `List.ResultList.<part>.Description`,
       `Part.Toolbar.Save`).
     - Design (WinForms/DotNetBar): ribbon buttons exposed by name ('Save',
       'Open', ...); the `Dimensions` field is a readable Edit; and the property
       grid `propertyGrid1` exposes named rows including 'User-defined' and
       'More...'. So the entire font chain is UIA -- no image templates.
     - The graphics canvas (`GraphicsWindow`) is opaque -> vision (below).
   Implemented in `navigator/boost_uia.py`.

2. **Canvas geometry** (choosing where the part-number goes). The drawing canvas
   is a rendered viewport -- UIA cannot see into it -- so this is *unavoidably
   vision*. This is the real crux behind the 95% bar and gets the most care:
   `vision/placement.py` and `vision/verify.py`.

## Placement: distance transform, not centroid

`vision/placement.py` segments the part body (outer boundary filled, holes
knocked out) and takes the **pole of inaccessibility** -- the interior point
farthest from any edge, via `cv2.distanceTransform`. That point's distance to the
nearest edge *is* the available clearance, so AutoBoost can reject a part when
there is genuinely no room, instead of silently placing too close to an edge.

The body itself is found by **nesting depth**, not by "largest enclosed region"
(0.7.7-0.7.8). Each free region is ranked by how many outline bands separate it
from the exterior (a breadth-first walk over region adjacency); material and
empty space alternate with depth, so the body is the union of the solid depths.
This excludes holes and large window cutouts, and -- critically -- the *void
between the part and the sheet/drawing boundary rectangle Boost draws around it
in Design view*. On a narrow part that void is the largest enclosed region, so
the old rule stencilled the number outside the part (`8604300I-1`); on a part
with big window cutouts the mirror failure put it inside a cutout
(`8576131EA2-1C`). The boundary adds one empty nesting level, detected by either
a hole-inside-enclosed-material witness (depth-3 region with a big depth-2
neighbour) or the outermost outline enclosing another of comparable filled size
(covers a part with no holes). Two real-screenshot lessons are baked in (0.7.8):
line-thickening is gentle so hole outlines in ~30px material strips don't weld
to the part/window edges and scramble the depths (heavy retry only if the gentle
outline leaks), and the vision crop must not slice the drawing (left fraction
0.16, matching the Design panel edge).

The first live run's overlays taught three more (0.7.9). Boost's boundary
rectangles are light GREY, not black, and a part can carry TWO of them nested
(drawing boundary + annotation plane) -- more parity shift than one offset can
absorb, which is how 8576131EA2-1D got its number stencilled inside a window and
8576131EA2-09 had the 16px boundary ring picked as its body. So segmentation
thresholds strictly on near-black first (`part_line_delta`): grey boundaries
never enter the outline no matter how many there are, with the legacy threshold
as fallback. Second, the coloured CAD origin marks (red X-axis line, green
Y-axis line, blue 0,0 dot) ride exactly along the part's bottom/left edges;
colour-saturated pixels therefore ALWAYS count as barriers, so an axis line that
overdraws a part edge keeps that edge sealed at every threshold instead of
opening a leak. Third, verify gained a low-contrast rescue: an engraving that
renders as a faint 1px stroke at Zoom Extents ("no marking change detected") is
re-detected at `verify_low_delta` with a component-area despeckle and FAILed
when the blob sits far from the body.

The second live run (0.7.10 logs + overlays, replayed against the actual PNGs)
drove 0.7.11. On one part, faint UI junk AT the crop edges (hint-text row, icon
strip, viewport frame line) walled three of the four borders at the legacy
threshold; the whole background then read as "enclosed", became the body, and
the number was stamped in the void again. Exterior regions are now seeded from a
10px margin band (`exterior_band_px`) that 1-3px edge artifacts cannot wall off,
and the strict threshold dropped to 120 (measured: part lines diff ~190,
boundary rects ~74). The part's REAL dimensions now also act as a sanity gate:
zoom is uniform, so the detected body's bbox aspect must match the real aspect
within 1.6x, else the placement aborts -- segmentation grabbed something that is
not the part. Verify was rebuilt around what the marking actually looks like:
the saved engraving is YELLOW (a ~200-point saturation jump but only ~19 grey
levels of darkening, invisible to a brightness diff), so detection is
darkening OR saturation-gain (`verify_sat_delta`); and the four false FAILs in
that run were re-rendered axis lines / hint text -- line-shaped components
(extreme bbox aspect, hollow line-work, or hugging the crop edge) are discarded
before judging.

Because the saved engraving is a WIDE, SHORT strip, placement reserves a
RECTANGLE of the text footprint rather than a circle (0.7.10): a point is valid
only if the whole footprint (erode the body by that rectangle) is clear, and
among valid points it takes the one with the most isotropic breathing room. The
footprint is sized in millimetres (font_height_mm, char_advance_ratio, number
length) and converted to pixels via the part's on-screen scale -- its body
bounding box in px against its real dimensions read from the Design 'Dimensions'
field -- so it tracks Zoom Extents. If the number can't fit anywhere the part is
aborted (flagged, not stamped). When dimensions or the number are unavailable it
falls back to the isotropic circle against `required_clearance_px`.
`char_advance_ratio` / `text_margin_frac` calibrate the footprint to a real saved
engraving.

Every vision module is runnable **standalone against a saved PNG** and emits a
debug overlay, so the algorithms can be iterated from screenshots without driving
live Boost -- the primary development feedback loop.

## Verify-after-save (new, closes the loop)

BoostPY never checked its work. `vision/verify.py` (planned) re-screenshots after
save, when the text has expanded to engraving geometry, and confirms the marking
does not intersect the boundary or any hole. On failure the part is undone and
retried or skipped -- this is what converts "hope" into measured >=95%.

## Orchestration

- `part_cycle.py` -- state machine for one part (open -> zoom -> place -> select
  -> font -> save -> verify -> close), with per-step diagnostics.
- `stencil_runner.py` -- the job loop: retry-per-part, skip-and-continue,
  consecutive-failure auto-stop, run statistics. Also holds the **duplicate
  guard**: an exact part number that recurs in the same sequence is stenciled
  once and its recurrences are skipped (not opened) and flagged in the end-of-run
  summary. Bones ported from BoostPY v0.01.20.
- `cut_runner.py` / `full_runner.py` -- the same loop shape for the cutting and
  combined (stencil-then-cut) jobs. `full_runner` composes the two single-process
  cycles per part; because each half starts and ends on Home they chain without a
  new mechanism, and the cut half is attempted only when the stencil half
  succeeded, so a part is never left half-finished.
- `reset.py` -- safe return-to-Home from any state (ESC/undo/close).
- `gui.py` -- tkinter control panel over the same loop. The job runs in a worker
  thread (STA COM, `sys.coinit_flags = 2`) that reports through a queue the Tk
  main loop drains; Cancel sets `stencil_runner.STOP`, the cooperative stop
  event all three runners already poll between parts, so a GUI stop is exactly
  as graceful as the 'q' kill switch (the current part finishes or recovers).
  On launch it runs `updater.check_for_update()` in a thread and offers to
  install (git fast-forward); a failed check logs one line and never blocks.
- `updater.py` -- stdlib-only version check against the clone's own branch
  (`git fetch` + `rev-list HEAD..origin/<branch>`) and `--ff-only` update.
  Any failure returns status "failed" rather than raising: updating is
  optional, running jobs is not.

## Module map

```
autoboost/
  __init__.py            app name + version (AutoBoost Beta 0.7.11)
  config.py              all tunables (dataclasses, JSON-loadable)
  logging_setup.py       versioned per-run logs + debug screenshots
  vision/
    placement.py         [built] safe placement via distance transform
    verify.py            [built] post-save collision check (clean/after diff)
  navigator/
    boost_uia.py         [built] UIA driver: parts list (scroll-aware), open,
                                 dims, font chain, cutting-program controls
  part_cycle.py          [built] stencil: one part (place -> font -> save -> verify)
  stencil_runner.py      [built] stencil job loop (open -> cycle -> close -> next)
  cut_cycle.py           [built] cutting: one part (new -> angular -> open ->
                                 apply -> save -> close)
  cut_runner.py          [built] cutting job loop over the Home list
  full_cycle.py          [built] combined: one part, stencil then cut
  full_runner.py         [built] combined job loop (stencil+cut per part)
  gui.py                 [built] tkinter control panel: Start/Cancel + live log
  updater.py             [built] launch-time version check + git ff update
tools/
  probe_uia.py           [built] dump Boost's UIA tree
  probe_open_dropdown.py [built] open + dump an owner-drawn dropdown
docs/
  ARCHITECTURE.md        this file
  BOOST_SETUP.md         required Boost/RDP settings for reliable vision
AutoBoost_Installer.bat  [built] one-file installer/updater (clone or ff-update,
                                 pip --user, Desktop shortcut; per-user, no admin)
```

## The Cut window (a second opaque surface)

The Design view is DotNetBar/WinForms; the **Cut** window
(`<part> - TruTops Boost - Cut`) is a different beast -- a Qt app
(`Qtitan::RibbonBar`, `TnQtWidgets`). Its **ribbon exposes no buttons to UIA**
(the probe's RibbonBar node has zero button children), so the auto-apply
cutting-technology button is a **positional click** in the maximized window
(`config.cut.apply_button_offset`), and the rest of the finish (dismiss notice,
save, close) is keyboard -- the same "chrome is opaque -> drive it by position"
situation as the drawing canvas. The Home-side cutting-program controls
(`Part.Detail.CutSolutions.*`), by contrast, are ordinary WPF UIA and driven by
auto_id.

## Current status

Beta 0.7.7 -- two validated tools plus a combined runner that chains them:

- **Grey boundaries, axis marks, faint markings (0.7.9)** -- from the first live
  run's auto-saved overlays: strict near-black thresholding keeps Boost's grey
  boundary rects (single or doubled) out of the outline entirely; colour-
  saturated pixels (red/green axis lines, blue origin dot) always act as
  barriers so an axis overdrawing a part edge can't open a leak; verify's
  low-contrast rescue catches a faint marking sitting off the part. Live-run
  scoreboard that drove this: -10/-08 correct, -09 wrong body but saved by the
  clearance gate, -1D number stencilled in a window with verify blind to it.
- **Placement robustness (0.7.7-0.7.8)** -- body detection moved from
  "largest enclosed region" to nesting-depth material extraction, fixing parts
  that stencilled the number outside the body (sheet-boundary void, 8604300I-1)
  or in a window cutout (8576131EA2-1C). 0.7.8 hardened it against the real
  screenshots: gentle morphology (thin-strip weld fix) with heavy retry,
  non-slicing crop, no-holes sheet detection, per-part placement overlays in
  `logs/<version>/`, and insufficient clearance now aborts the part. Verify's
  "inconclusive" guard distinguishes an edge sliver from a marking sitting far
  from the body, and a hard verify FAIL skips the cut and flags the part instead
  of cutting a mis-marked blank. Regression tests in
  `tests/test_placement_frame.py` cover plain / sheet-bounded / windowed /
  big-part geometry plus a faithful full-screenshot replica of 8576131EA2-1C,
  all against the live vision code.


- **Stenciling** -- an 11/11-part job ran unattended with zero skips. The font
  chain (the hardest piece) is fully automated: `add_font_type` (keyboard
  through the property selector) and `set_font_by_drag` (held mouse-drag on the
  owner-drawn value list, the only gesture that control honours).
- **Cutting programs** -- a full 17-part job ran end to end. The Home controls
  are UIA (`Part.Detail.CutSolutions.*`); the Cut window's ribbon is positional (the click focuses + maximizes the
window first so the left-anchored offset holds).
- **Combined (0.7.1)** -- `full_runner` does both per part (stencil then cut)
  before advancing. It reuses the two validated cycles unchanged; the only new
  code is the composition and a recover step that can close whichever window (Cut
  or Design) a failed part left open.
- **Control panel (0.7.4)** -- `gui` runs any of the three modes from a window
  (Start / graceful Cancel / live log with save-to-file), wrapping
  `run_full_job` unchanged.
- **Auto-update check (0.7.5)** -- every GUI launch checks the branch's remote
  and asks before fast-forwarding; a failed check is one log line, never a
  blocker. The GUI's font/angular fields were removed (fixed to the validated
  shop standard); the CLI keeps the flags.
- **Installer (0.7.6)** -- `AutoBoost_Installer.bat` is the one file that installs or
  updates everything: verify py/git (it cannot install them without admin, so
  a missing one is a clear message + exit 1), fast-forward the clone it sits
  in (or clone fresh into `%USERPROFILE%\AutoBoost` when run standalone),
  `pip install --user` the requirements, and best-effort create a Desktop
  shortcut to `pythonw -m autoboost.gui`. Idempotent; a clone with local
  changes is warned about, never clobbered. On-workstation validation pending.

Notable fixes on the way: the Home parts list is virtualized, so `parts()` /
`select_part()` scroll to enumerate/reach every row; and the angular-positions
step checks the value IS the last option (Boost remembers the last-used value
as the default) rather than that it changed.

Versioning: each shipped iteration bumps the patch by 0.0.1 (0.5.0 -> 0.5.1).

Roadmap (see README): further stencil speed gains; a cross-run "already done"
guard; calibrate `required_clearance_px`; confirm the list-scroll direction per
machine. (0.5.8-0.5.9 hardened the Cut auto-apply click: force the window to
fill the screen via Win32 ShowWindow/MoveWindow -- the UIA maximize doesn't
resize this Qt window -- and refuse a too-narrow window.)
