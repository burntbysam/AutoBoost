# AutoBoost Architecture (0.02.01)

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
   These are *widgets*. Preferred driver is **UI Automation (UIA)** via
   `pywinauto`, which reads control identity server-side and is immune to RDP
   blur and resolution changes -- directly killing the "clicked 'Mode' instead
   of 'More...'" class of failure. Whether Boost exposes a usable UIA tree is
   unknown until `tools/probe_uia.py` is run; the navigator is therefore an
   abstraction with two implementations:
     - `navigator/uia_nav.py`   (preferred, pending probe result)
     - `navigator/vision_nav.py` (fallback: region-restricted template matching,
       ported/hardened from BoostPY v0.01.20)

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

The clearance threshold (`PlacementConfig.required_clearance_px`) must be
calibrated to the on-screen footprint of the ~3x expanded text. It is currently
a placeholder and will be tuned from real screenshots.

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
- `runner.py` -- the job loop: retry-per-part, skip-and-continue, consecutive-
  failure auto-stop, run statistics. Bones ported from BoostPY v0.01.20.
- `reset.py` -- safe return-to-Home from any state (ESC/undo/close).

## Module map

```
autoboost/
  __init__.py            app name + version (AutoBoost_0.02.01)
  config.py              all tunables (dataclasses, JSON-loadable)
  logging_setup.py       versioned per-run logs + debug screenshots
  vision/
    placement.py         [built]   safe placement via distance transform
    verify.py            [built]   post-save collision check (before/after diff)
    text_detect.py       [planned] locate placed yellow text (port + harden)
  navigator/
    base.py              [planned] navigator interface
    uia_nav.py           [planned, pending probe] pywinauto implementation
    vision_nav.py        [planned] template-matching fallback
  part_cycle.py          [planned] one-part state machine
  runner.py              [planned] job loop
  reset.py               [planned] return-to-Home recovery
tools/
  probe_uia.py           [built] dump Boost's UIA tree (decides navigator)
docs/
  ARCHITECTURE.md        this file
  BOOST_SETUP.md         required Boost/RDP settings for reliable vision
```

## Current status / next inputs needed

Foundation, placement, and the UIA probe are in. To choose the navigator and
calibrate placement we need, from the RDP machine:

1. Output of `tools/probe_uia.py` (decides UIA vs. vision navigation).
2. A few representative Design-View screenshots after Zoom Extents -- a simple
   part, a part with holes/cutouts, and an awkward/thin part -- at the real RDP
   resolution, to tune `placement.py` and calibrate `required_clearance_px`.
