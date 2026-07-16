# AutoBoost

**AutoBoost Beta 0.7.17** — GUI automation for repetitive per-part chores in
TRUMPF TruTops Boost.

## Download

**[Download `AutoBoost_Installer.bat`](https://github.com/burntbysam/AutoBoost/releases/download/v0.7.6/AutoBoost_Installer.bat)**
— save it anywhere, then double-click it on the Boost workstation. That one
file installs AutoBoost on a fresh machine or updates an existing install,
entirely per-user with no admin rights needed. (From the
[v0.7.6 release](https://github.com/burntbysam/AutoBoost/releases/tag/v0.7.6);
see [Releases](https://github.com/burntbysam/AutoBoost/releases) for other
versions.) Full details in [Install](#install-on-the-rdp-workstation-no-admin-needed)
below.

AutoBoost drives the Boost GUI, unattended, across every part in a job. Two
tools today, runnable separately or fused into one pass:

1. **Part-number stenciling** — place a correctly-fonted (EasyType-L=10mm)
   part-number engraving on each part.
2. **Cutting-program creation** — create + apply a cutting program on each
   part (New → set angular positions → auto-apply technology → save → close).
3. **Combined** (0.7.x) — for each part, do the stencil **then** the cutting
   program before advancing, so a part leaves the run fully finished.

You put Boost on the Home screen, launch a runner, walk away, and come back to
a finished job.

> **Status:** Beta. The stencil job ran 11/11 unattended; the cutting job ran a
> full 17-part list. Runs on the Windows/RDP workstation where Boost is visible
> — this repo is the codebase; the bot executes there.

## What it does, per part

Open the part → Zoom Extents → find a safe placement point (vision) → place the
part-number → zoom in and select it → add the `Font type` property → set
`EasyType-L=10mm` → deselect → save (text becomes engraving geometry) → verify
the marking is clear of edges/holes → close → next part.

- **Placement** picks the point of maximum clearance inside the part body (holes
  and cutouts excluded), so the number never lands in a slot, hole, or against an
  edge. Because the engraving is a wide, short strip, placement reserves a
  RECTANGLE matching the text footprint (0.7.10), not a circle -- sized from the
  part's real dimensions (read via UIA) and the number's length, so it scales
  with zoom. It takes the roomiest spot the rectangle fits and aborts the part if
  the number can't fit anywhere clear. `char_advance_ratio` and `text_margin_frac`
  in `config.py` calibrate the footprint against a real saved engraving.
- **Placement self-checks against reality** (0.7.11): the exterior is seeded
  from a margin band so UI artifacts at the crop edges can't invert the body,
  and the detected body's aspect ratio must match the part's real dimensions
  (within 1.6x) or the part is aborted instead of stamped.
- **Verify sees the marking as it really renders** (0.7.11): the saved
  engraving is yellow, so it's detected by colour-saturation gain as well as
  darkening, and line-shaped re-render artifacts (axis lines, the hint-text
  row) are discarded instead of being flagged as out-of-body markings.
- **Verify is gated to the placement point** (0.7.12): only changes near the
  spot the number was actually stamped count as the marking. UI chrome that
  re-renders between the two frames (the tab-bar title gaining its modified
  marker, the bottom icon strip, 1px-shifted viewport frame lines) failed
  five correctly-placed parts in a row; those diffs are now ignored, while a
  stamp in the void still fails because it appears AT the expected point,
  far from the part body.
- **Navigation** (parts list, open/save/close, the Properties/font chain) is
  driven by Windows UI Automation where possible — no fragile image templates —
  with mouse/keyboard for the two owner-drawn dropdowns and the drawing canvas.

## Install (on the RDP workstation, no admin needed)

One file does everything — first install and every later update:

- **Already have the repo?** Double-click **`AutoBoost_Installer.bat`** in the repo folder.
  It fast-forwards the clone to the latest version (same semantics as the GUI's
  update check), upgrades the dependencies (`pip --user`), and puts an
  "AutoBoost" shortcut on the Desktop.
- **Fresh machine?** Copy just `AutoBoost_Installer.bat` over and double-click it. It
  clones the repo into `%USERPROFILE%\AutoBoost` and does the same. Python 3
  (the `py` launcher) and Git for Windows must already be installed — the
  installer can't add those without admin; it detects a missing one and tells
  you to get it from the company software portal.

It's safe to re-run any time; a clone with local changes is never overwritten
(the update is skipped with a warning instead). Manual fallback:

```
pip install --user -r requirements.txt
```
If `python` isn't found, use `py` (the launcher) for every command below.

Lock down the Boost/RDP settings in [`docs/BOOST_SETUP.md`](docs/BOOST_SETUP.md)
first — the vision is tuned to a crisp, 100%-scale, maximized Boost window.

## Run a job

Put Boost on the **Home** screen, then run one of the three job runners. Each
counts down 5 s, processes every part, and prints a `done/skipped` tally.
**Kill switch:** Ctrl+C, or hold `q` (needs the `keyboard` package).

**Control panel (GUI):**
```
py -m autoboost.gui          # window with Start/Cancel and a live log
pyw -m autoboost.gui         # same, without a console window
```
All three jobs from one window: pick the mode (stencil + cut / stencil only /
cut only), optionally list specific parts, hit **Start**. The log pane shows
exactly what the console runners would print, and **Save Log…** writes it to a
file. **Cancel** is graceful — the run stops before the *next* part, so the
current part finishes (or recovers to Home) and nothing is left half-done.
Uses tkinter (ships with Python): nothing extra to install.

On every launch the panel **checks for a newer version** (a git fetch of this
branch) and asks before installing it; after an update, relaunch to run the
new code. If the check can't run — offline, git trouble, anything — it logs
"Version check failed" and the tool works as-is. The font
(`EasyType-L=10mm`) and angular positions (last option) are fixed to the
validated shop standard in the GUI; the CLI runners keep `--font`/`--angular`
for calibration work.

**Part-number stenciling:**
```
py -m autoboost.stencil_runner                       # every part in the Home list
py -m autoboost.stencil_runner --parts 8604300I-1 8604301I-1   # just these
py -m autoboost.stencil_runner --no-save --no-close  # dry mechanics (no save/close)
```

**Cutting-program creation:**
```
py -m autoboost.cut_runner                   # every part in the Home list
py -m autoboost.cut_runner --parts 8604300I-1 8604301I-1
py -m autoboost.cut_runner --no-finish       # open each Cut window only
```

**Combined (stencil + cut, per part):**
```
py -m autoboost.full_runner                  # every part: stencil then cut
py -m autoboost.full_runner --parts 8604300I-1 8604301I-1
py -m autoboost.full_runner --stencil-only   # same loop, stencil phase only
py -m autoboost.full_runner --cut-only       # same loop, cut phase only
```
For each part it finishes the stencil (open → place → font → save → verify →
close) and then immediately the cutting program before advancing, so a part
leaves the run fully done. A part whose stencil fails is **not** cut — it's
skipped whole and flagged, never left half-finished.

Per part the cutting half: create a cutting program → set `Allowed angular
positions (Job)` to the last option (`0°;90°...`) → open the Cut window → click
auto-apply technology → save → close back to Home. The Home half is UIA; the Cut window is
a Qt app whose ribbon is invisible to UIA, so the auto-apply button is a
positional click (`config.cut.apply_button_offset`) and the finish is keyboard.

## Run / tune individual pieces

Useful for testing and calibration:

```
# One part already open in Design view:
py -m autoboost.part_cycle [--save] [--close]

# UIA driver checks (Design view open, part-number text selected):
py -m autoboost.navigator.boost_uia --selftest
py -m autoboost.navigator.boost_uia --add-font
py -m autoboost.navigator.boost_uia --set-font-drag [--dry-run]
py -m autoboost.navigator.boost_uia --open-part "8604300I-1"   # from Home

# Vision, against a saved screenshot (no live Boost needed):
py -m autoboost.vision.placement shot.png          # writes shot.placement.png
py -m autoboost.vision.verify before.png after.png # writes after.verify.png

# UIA tree diagnostics:
py tools/probe_uia.py --find barLeftDockSite --out dump.txt

# Cutting program, one part (from Home):
py -m autoboost.cut_cycle --part 8604300I-1        # full create + apply + close
py -m autoboost.cut_cycle --part 8604300I-1 --no-finish   # open Cut window only
py -m autoboost.cut_cycle --locate                 # read-only: report controls
py -m autoboost.navigator.boost_uia --cut-apply --dry-run  # hover the apply btn

# Combined stencil + cut, one part (from Home):
py -m autoboost.full_cycle --part 8604300I-1                  # both phases
py -m autoboost.full_cycle --part 8604300I-1 --stencil-only   # stencil only
py -m autoboost.full_cycle --part 8604300I-1 --cut-only       # cut only
```

## Configuration

- `autoboost/config.py` — vision/timing constants (canvas crop, placement
  clearance, sleeps). JSON-loadable for a per-machine profile.
- `autoboost/part_cycle.py` — `SELECT_SCROLL` is the zoom-to-select scroll
  amount; it is **negative to zoom in** on the reference setup. If your Boost
  zooms *out* on select, flip the sign.
- Font list order lives in `BoostUIA.FONT_OPTIONS` — update it if the shop's
  font table changes.

## Duplicate guard

If the same exact part number appears more than once in a run's sequence, every
runner processes it **once** — the first occurrence — and skips every recurrence
without opening it (no double-stamp / no second cutting program). Each skipped
duplicate is listed in a `*** FLAG` block in the end-of-run summary so you can
reconcile the job. This is a name check against the Home list only; it does not
detect work done in a *previous* run (see roadmap).

## Known limitations / roadmap

- **Speed.** Trimmed in 0.5.1 (settle delays cut where safe). Further gains need
  reducing the font-chain screenshots/retries, measured on the live machine.
- **No cross-run "already stenciled" guard.** The duplicate guard is per-run
  (same sequence). Re-running a part that was stenciled in an *earlier* run adds
  a second number (placement is deterministic). Detecting existing markings on
  open is planned.
- **Clearance floor.** The placement minimum (`required_clearance_px`) is a
  conservative constant; calibrating it to the part-number length (so very tight
  parts are flagged up front) is planned. Verify still catches a real collision.
- **Sheet-boundary voids and large cutouts (fixed 0.7.7-0.7.8).** Boost draws a
  sheet/drawing boundary rectangle around the part in Design view. On a narrow
  part the void between that boundary and the part is an enclosed region larger
  than the part, so the old "largest enclosed region" rule stencilled the number
  *outside* the part (8604300I-1). Parts with large window cutouts had the mirror
  problem -- the number could land in a cutout (8576131EA2-1C). Placement now
  classifies every enclosed region by nesting depth (how many outline bands
  separate it from the exterior); material and empty space alternate with depth,
  so the body is the union of the solid depths, which excludes the exterior,
  holes, cutouts, and the boundary void. 0.7.8 made this hold on real screenshots:
  gentle line-thickening so hole outlines in thin (~30px) material strips don't
  weld to the part/window edges and corrupt the topology (with a heavy retry if
  the gentle outline leaks), a crop that no longer slices the drawing (left 0.16,
  clearing the Design panel without eating the sheet boundary), sheet detection
  that also works on a part with no holes (the outermost outline enclosing
  another of comparable filled size), placement debug overlays auto-saved to
  `logs/<version>/` on every part, and an insufficient-clearance placement now
  aborts the part instead of stamping too close to an edge. If a bad placement
  ever slips through anyway, verify FAILs it and the part is flagged and not cut.
  0.7.9 (from the first live run's overlays): Boost's boundary rectangles render
  light grey, not black -- and a part can carry TWO, nested (drawing boundary +
  annotation plane), which shifted the parity beyond what one offset could fix
  (8576131EA2-1D stencilled its number inside a window; 8576131EA2-09 picked the
  16px boundary ring and was saved only by the clearance gate). Segmentation now
  thresholds strictly on near-black first, so grey boundaries never enter the
  outline no matter how many there are, falling back to the legacy threshold only
  if nothing is found. The coloured CAD origin marks (red X-axis line, green
  Y-axis line, blue 0,0 dot) ride exactly along the part's bottom/left edges, so
  colour-saturated pixels always count as barriers -- an axis overdrawing an edge
  keeps it sealed instead of opening a leak. And verify gained a low-contrast
  rescue pass: a saved engraving that renders as a faint 1px stroke at Zoom
  Extents (previously "no marking change detected") is re-detected at a lower
  threshold and FAILed when it sits far from the part body.
- **Verify gates only the clear-miss case.** The post-save check logs PASS/FAIL
  and is advisory for borderline placements (placement already guaranteed
  clearance). As of 0.7.3 it no longer prints a false FAIL when the before/after
  diff catches only a tiny sliver of change (`text_px < 60` and ~all of it at an
  edge) -- reported as "inconclusive, assumed clear." As of 0.7.7 that
  inconclusive path is split by DISTANCE from the part: a tiny changed region
  hugging the body edge is still assumed clear, but one sitting far from the body
  (the marking landed in the void beside the part) is a hard FAIL, and a hard
  FAIL now skips the cut and flags the part instead of cutting it.
- **Cut auto-apply click is positional.** The Cut ribbon is invisible to UIA, so
  the auto-apply button is clicked by a fixed offset. The click first forces the
  Cut window to fill the screen via Win32 `ShowWindow`/`MoveWindow` (the UIA
  maximize alone doesn't resize this Qt window — it only flips the state flag),
  so the left-anchored offset holds regardless of screen width, and it refuses
  to click a window narrower than `config.cut.min_ribbon_width`. Remaining edge:
  a genuinely different ribbon skin/resolution would still need the offset
  re-tuned.
- **List scroll assumes wheel-up = toward top.** `parts()`/`select_part()`
  wheel-scroll the virtualized Home list; if a machine scrolls the other way,
  flip the sign in `BoostUIA._scroll_list`.

## Versioning

Each iteration increments the patch by 0.0.1 (0.5.0 → 0.5.1 → 0.5.2). Minor
bumps mark milestones: 0.5.9 was the last of the cutting-program line (stencil +
cutting tools validated); 0.7.0 opened the current line; 0.7.1 fuses the two
tools into a combined per-part runner (`full_runner`); 0.7.4 adds the control
panel (`gui`); 0.7.6 adds the one-file installer (`AutoBoost_Installer.bat`).

## Layout

- `autoboost/` — package (config, logging, `vision/`, `navigator/`)
  - `part_cycle` + `stencil_runner` — part-number stenciling (one part / whole job)
  - `cut_cycle` + `cut_runner` — cutting-program creation (one part / whole job)
  - `full_cycle` + `full_runner` — combined stencil-then-cut (one part / whole job)
  - `gui` — tkinter control panel (Start/Cancel, live log, log save) over the same job loop
  - `updater` — launch-time version check + git fast-forward update (best-effort, never blocks)
- `AutoBoost_Installer.bat` — one-file installer/updater (fresh clone or fast-forward + `pip --user` + Desktop shortcut)
- `tools/` — UIA probes
- `docs/ARCHITECTURE.md` — design and module map
- `docs/BOOST_SETUP.md` — required Boost/RDP settings

Formerly the flat `BoostPY` script; rebuilt as a structured, UIA-first tool.
