# AutoBoost Architecture (Beta 0.5.7)

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
  failure auto-stop, run statistics. Also holds the **duplicate guard**: an
  exact part number that recurs in the same sequence is stenciled once and its
  recurrences are skipped (not opened) and flagged in the end-of-run summary.
  Bones ported from BoostPY v0.01.20.
- `reset.py` -- safe return-to-Home from any state (ESC/undo/close).

## Module map

```
autoboost/
  __init__.py            app name + version (AutoBoost Beta 0.5.7)
  config.py              all tunables (dataclasses, JSON-loadable)
  logging_setup.py       versioned per-run logs + debug screenshots
  vision/
    placement.py         [built] safe placement via distance transform
    verify.py            [built] post-save collision check (clean/after diff)
  navigator/
    boost_uia.py         [built] UIA driver: parts list (scroll-aware), open,
                                 dims, font chain, cutting-program controls
  part_cycle.py          [built] stencil: one part (place -> font -> save -> verify)
  runner.py              [built] stencil job loop (open -> cycle -> close -> next)
  cut_cycle.py           [built] cutting: one part (new -> angular -> open ->
                                 apply -> save -> close)
  cut_runner.py          [built] cutting job loop over the Home list
tools/
  probe_uia.py           [built] dump Boost's UIA tree
  probe_open_dropdown.py [built] open + dump an owner-drawn dropdown
docs/
  ARCHITECTURE.md        this file
  BOOST_SETUP.md         required Boost/RDP settings for reliable vision
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

Beta 0.5.7 -- two validated tools:

- **Stenciling** -- an 11/11-part job ran unattended with zero skips. The font
  chain (the hardest piece) is fully automated: `add_font_type` (keyboard
  through the property selector) and `set_font_by_drag` (held mouse-drag on the
  owner-drawn value list, the only gesture that control honours).
- **Cutting programs** -- a full 17-part job ran end to end. The Home controls
  are UIA (`Part.Detail.CutSolutions.*`); the Cut window's ribbon is positional.

Notable fixes on the way: the Home parts list is virtualized, so `parts()` /
`select_part()` scroll to enumerate/reach every row; and the angular-positions
step checks the value IS the last option (Boost remembers the last-used value
as the default) rather than that it changed.

Versioning: each shipped iteration bumps the patch by 0.0.1 (0.5.0 -> 0.5.1).

Roadmap (see README): further stencil speed gains; a cross-run "already done"
guard; calibrate `required_clearance_px`; harden the Cut auto-apply click
against a non-maximized window; confirm the list-scroll direction per machine.
