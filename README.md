# AutoBoost

**AutoBoost_0.02.01** — automated part-number stenciling for TRUMPF TruTops Boost
CNC programming software.

AutoBoost drives the Boost GUI to place a correctly-fonted (EasyType-L=10mm)
part-number engraving on every part in a job — unattended, targeting ≥95%
correct placements. It is the rebuilt successor to the flat `BoostPY` script,
restructured for reliability and for development without a live Boost session.

> Boost exposes no automation API/COM/macro for UI actions, so AutoBoost is a
> GUI automation tool. It runs on the Windows/RDP workstation where Boost is
> visible. **This repository is the codebase; the bot must be run there.**

## Layout

- `autoboost/` — the package (config, logging, vision, navigation, orchestration)
- `tools/probe_uia.py` — one-off diagnostic: does Boost expose a UI Automation
  tree? Its result decides how menus are driven. **Run this first.**
- `docs/ARCHITECTURE.md` — design and module map
- `docs/BOOST_SETUP.md` — Boost/RDP settings required for reliable vision

## Install (on the RDP workstation, no admin needed)

```
pip install --user -r requirements.txt
```

## Try the pieces that work from a screenshot alone

Placement can be tuned from a saved PNG, no live Boost required:

```
python -m autoboost.vision.placement your_design_view_screenshot.png
```

It prints the chosen point and clearance and writes a `*.placement.png` overlay.

## Status

Foundation, safe-placement vision, and the UIA probe are in place. Next steps and
the inputs needed from the machine are listed at the bottom of
`docs/ARCHITECTURE.md`.
