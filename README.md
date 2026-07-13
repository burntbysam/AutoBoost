# AutoBoost

**AutoBoost Beta 0.7.2** — GUI automation for repetitive per-part chores in
TRUMPF TruTops Boost.

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

- **Placement** uses a distance transform to pick the point of maximum clearance
  inside the part body (holes excluded), so the number never lands in a slot,
  hole, or against an edge.
- **Navigation** (parts list, open/save/close, the Properties/font chain) is
  driven by Windows UI Automation where possible — no fragile image templates —
  with mouse/keyboard for the two owner-drawn dropdowns and the drawing canvas.

## Install (on the RDP workstation, no admin needed)

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
tools into a combined per-part runner (`full_runner`).

## Layout

- `autoboost/` — package (config, logging, `vision/`, `navigator/`)
  - `part_cycle` + `stencil_runner` — part-number stenciling (one part / whole job)
  - `cut_cycle` + `cut_runner` — cutting-program creation (one part / whole job)
  - `full_cycle` + `full_runner` — combined stencil-then-cut (one part / whole job)
- `tools/` — UIA probes
- `docs/ARCHITECTURE.md` — design and module map
- `docs/BOOST_SETUP.md` — required Boost/RDP settings

Formerly the flat `BoostPY` script; rebuilt as a structured, UIA-first tool.
