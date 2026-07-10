# AutoBoost

**AutoBoost Beta 0.5.0** — automated part-number stenciling for TRUMPF TruTops
Boost.

AutoBoost drives the Boost GUI to place a correctly-fonted (EasyType-L=10mm)
part-number engraving on every part in a job — unattended. You select a job,
launch it, walk away, and come back to every part stenciled.

> **Status:** Beta. A full 11/11-part job completed unattended with zero
> skips. Runs on the Windows/RDP workstation where Boost is visible — this repo
> is the codebase; the bot executes there.

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

Put Boost on the **Home** screen, then:

```
py -m autoboost.runner                       # every part in the Home list
py -m autoboost.runner --parts 8604300I-1 8604301I-1   # just these
py -m autoboost.runner --no-save --no-close  # dry mechanics (no save/close)
```
It counts down 5 s, then processes each part and prints a `done/skipped` tally.
**Kill switch:** Ctrl+C, or hold `q` (needs the `keyboard` package).

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
```

## Configuration

- `autoboost/config.py` — vision/timing constants (canvas crop, placement
  clearance, sleeps). JSON-loadable for a per-machine profile.
- `autoboost/part_cycle.py` — `SELECT_SCROLL` is the zoom-to-select scroll
  amount; it is **negative to zoom in** on the reference setup. If your Boost
  zooms *out* on select, flip the sign.
- Font list order lives in `BoostUIA.FONT_OPTIONS` — update it if the shop's
  font table changes.

## Known limitations / roadmap

- **Speed.** ~1 min/part (UIA + deliberate settle delays). Trimming the sleeps
  and drag retries is the next task.
- **No "already stenciled" guard.** Re-running a part adds a second number
  (placement is deterministic). Run on un-stenciled parts; a skip guard is
  planned.
- **Clearance floor.** The placement minimum (`required_clearance_px`) is a
  conservative constant; calibrating it to the part-number length (so very tight
  parts are flagged up front) is planned. Verify still catches a real collision.

## Layout

- `autoboost/` — package (config, logging, `vision/`, `navigator/`,
  `part_cycle`, `runner`)
- `tools/` — UIA probes
- `docs/ARCHITECTURE.md` — design and module map
- `docs/BOOST_SETUP.md` — required Boost/RDP settings

Formerly the flat `BoostPY` script; rebuilt as a structured, UIA-first tool.
