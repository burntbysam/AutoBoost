# Boost / RDP setup for reliable automation

AutoBoost's vision is tuned to a specific on-screen appearance. The more stable
and crisp the RDP session, the higher the success rate. Lock these down before a
run; a change here silently degrades detection.

## RDP session

- **Experience level: LAN / highest quality.** Avoids aggressive colour
  compression that washes out the yellow part-number text and blurs thin edges.
- **Persistent bitmap caching: OFF.** Cached tiles can serve stale pixels and
  create phantom matches.
- **Display scaling: 100%.** All pixel constants assume 1:1. Scaling invalidates
  them.
- **Fixed resolution.** Keep the same RDP resolution between runs; if it changes,
  re-tune the canvas region and clearance in `config.py`.
- **Font smoothing: ON.** Helps text detection.

## Boost window

- **Maximized and unobstructed.** No overlapping windows; consistent panel
  layout. The canvas crop in `config.py` (`CanvasRegion`) assumes the left
  Properties/parts panel and top toolbar are in their normal docked positions.
- **Design View gridlines: OFF (recommended).** Gridlines add spurious edges the
  geometry segmentation can latch onto. If gridlines must stay on, we will need
  to filter them explicitly -- flag this.
- **Start state.** Home view, first part in the "PARTS (XXXX)" list selected,
  before launching AutoBoost.

## Boost specifics to confirm (please verify on the machine)

These are used by AutoBoost and should be checked against your configuration:

- Hotkeys: `Z` = Zoom Extents, `1` = Part Number tool, `2` = Save, `3` = Close
  Design View. These are user/shop-configured; confirm they match.
- The part number is auto-filled by Boost from the drawing filename when the
  Part Number tool is activated (AutoBoost does not type it).
- Font/technology target: EasyType-L=10mm as the engraving font applied via the
  Properties -> User-defined -> "More..." chain.
- Boost version (Help -> About). Behaviour can differ between releases; record it
  so the navigator can account for layout differences.

## Calibration artifacts we need

To tune the vision from screenshots (no live Boost needed for this part):

1. Design-View screenshots **after Zoom Extents**, full RDP viewport, for:
   - a simple solid part,
   - a part with holes / cutouts,
   - a thin or awkward part where safe placement is hard.
2. If possible, one screenshot **after save** showing the ~3x expanded engraving
   text, so we can measure its on-screen footprint and set the required
   placement clearance correctly.
