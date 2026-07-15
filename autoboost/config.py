"""Central configuration for AutoBoost.

Everything tunable lives here so the vision/timing constants are not scattered
through the code the way they were in the flat BoostPY script. A Config can be
loaded from / saved to JSON so a machine-specific profile (RDP resolution, panel
layout, timing) can be kept next to the code without editing source.

Coordinates and sizes are in absolute screen pixels of the RDP session viewport
unless noted otherwise. If the RDP resolution or display scaling changes, the
geometry-related values here must be re-tuned.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


@dataclass
class CanvasRegion:
    """The Design-View drawing canvas as fractions of the full screen.

    Used to crop UI chrome (left Properties panel, top toolbar, status bars)
    out of the screenshot before doing geometry vision, so dimension text and
    panel widgets can't be mistaken for part geometry.
    """

    # The Design-view left panel ends at ~300px on the reference 1920-wide setup,
    # and at Zoom Extents the sheet/drawing boundary can start as little as ~50px
    # right of it. left=0.20 (384px) sliced through that boundary -- and even
    # through the part's own edge on wide parts -- making the material region
    # touch the crop border and read as exterior. 0.16 (307px) clears the panel
    # without eating the drawing.
    left: float = 0.16    # skip the left Properties/parts panel
    top: float = 0.08     # skip the top toolbar/ribbon
    right: float = 0.02   # small right margin
    bottom: float = 0.06  # skip the bottom status bar

    def to_pixels(self, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
        """Return (x1, y1, x2, y2) pixel rectangle for this canvas region."""
        x1 = int(screen_w * self.left)
        y1 = int(screen_h * self.top)
        x2 = int(screen_w * (1.0 - self.right))
        y2 = int(screen_h * (1.0 - self.bottom))
        return x1, y1, x2, y2


@dataclass
class PlacementConfig:
    """Parameters for the safe-placement vision (see vision/placement.py)."""

    # Geometry is extracted by how far a pixel's brightness differs from the
    # canvas background, NOT by Canny edges. Real Boost screens have a faint
    # square grid behind the part; a plain edge detector latches onto it and
    # shatters the interior. A background-difference threshold keeps the strong
    # dark part geometry and ignores the low-contrast grid. Works for either
    # polarity (dark-on-light or light-on-dark) because it uses |value - bg|.
    geometry_delta: int = 80
    # PART geometry is drawn near-black; the boundary rectangles Boost adds
    # around the part (drawing boundary, annotation plane -- a part can carry
    # BOTH, nested) render light grey. Segmentation uses this stricter
    # threshold first so those boundaries never enter the outline at all:
    # with them in, they wall off phantom enclosed rings that either capture
    # the placement (8576131EA2-09) or shift the nesting parity so the body
    # comes out inverted (8576131EA2-1D stencilled its number inside a window
    # cutout). If nothing is found at this threshold -- a machine that renders
    # geometry lighter -- segmentation falls back to geometry_delta. Measured
    # on the workstation (0.7.10 run): part lines ~58 grey (diff ~190), the
    # boundary rects ~174 grey (diff ~74) -- 120 keeps a wide margin on both
    # sides while tolerating lines antialiased lighter than the 150 that made
    # the strict pass leak on 8604300I-1.
    part_line_delta: int = 120
    # Free regions overlapping this many pixels of the crop's outer edge count
    # as exterior, not only regions touching the exact border row/col. Live
    # 8604300I-1 failure: faint UI junk AT the crop edges (hint-text row, icon
    # strip, viewport frame line) walled three borders at the legacy threshold,
    # so the whole background read as "enclosed", became the body, and the
    # number was stamped in the void. A margin band can't be walled off by
    # 1-3px edge artifacts.
    exterior_band_px: int = 10
    # Verify-only low-contrast threshold. At Zoom Extents a saved engraving can
    # be a 1px antialiased stroke that never crosses geometry_delta ("no marking
    # change detected"); the rescue pass re-looks at this delta so a marking
    # that landed OFF the part is still caught.
    verify_low_delta: int = 40
    # The saved engraving renders YELLOW on the MarkedText layer (live 0.7.10
    # frames): its brightness barely differs from the canvas (~19 grey levels,
    # invisible to a darkness diff) but its colour saturation jumps ~200.
    # Verify therefore also detects the marking as a saturation INCREASE of at
    # least this much between the clean and post-save frames.
    verify_sat_delta: int = 60
    # Boost draws the CAD origin markers in colour: red X-axis line, green
    # Y-axis line, blue 0,0 dot -- and the axis lines ride exactly along the
    # part's bottom/left edges (the origin is the part corner). Real geometry
    # and the grey boundary rects are colourless, so saturation separates them:
    # any pixel at least this saturated is ALWAYS treated as a barrier,
    # regardless of brightness. That keeps an axis line that overdraws a part
    # edge acting as that edge (no leak at any threshold), while never letting
    # the grey boundaries back into the outline.
    axis_saturation_min: int = 60
    # Dilation/close to seal small gaps in the outline so regions are watertight.
    # GENTLE by default: hex holes sitting in a ~30px material strip weld to the
    # neighbouring part/window edges at 2 iterations, corrupting the region
    # topology (the 8576131EA2-1C number-in-a-window bug). One iteration seals
    # ~4px of gap and keeps the strips open; placement escalates to heavy
    # morphology on its own if the gentle outline leaks.
    close_kernel: int = 3
    dilate_iterations: int = 1
    close_iterations: int = 1
    # Ignore contours smaller than this many pixels (noise / tiny dimension marks).
    min_contour_area: int = 2000
    # Extra safety erosion of the valid body mask, in pixels, before the
    # distance transform. Accounts for line thickness and small render blur.
    body_erode_px: int = 3
    # Minimum clearance (radius, px) required at the chosen point when the text
    # footprint is UNKNOWN (no part dimensions / part number available). Used only
    # by the isotropic-circle fallback; the rectangular path below supersedes it.
    required_clearance_px: int = 45

    # --- Part-number text footprint (rectangular clearance) ---
    # The saved engraving is a WIDE, SHORT strip, not a disc: EasyType-L is 10mm
    # tall and each glyph advances ~0.7x its height, so an N-character number is
    # about 0.7*N*10mm wide by 10mm tall. Placement reserves a RECTANGLE of that
    # footprint (plus a margin) and requires it clear of every edge/hole/cutout,
    # instead of a circle that would over-reserve in Y and under-reserve in X.
    # The rectangle is sized in millimetres and converted to pixels using the
    # on-screen scale derived from the part's REAL dimensions (read via UIA) and
    # its on-screen size, so it tracks Zoom Extents automatically. Falls back to
    # the isotropic circle above when dimensions or the part number are missing.
    # char_advance_ratio and text_margin_frac are the two calibration knobs.
    font_height_mm: float = 10.0         # EasyType-L cap height
    char_advance_ratio: float = 0.7      # glyph advance width / height
    text_margin_frac: float = 0.4        # extra clearance each side, as a
                                         # fraction of the text height


@dataclass
class TimingConfig:
    """Sleep durations (seconds) between UI actions.

    Screen-scraping needs the UI to settle between steps. These mirror the
    values that were found to work in BoostPY v0.01.20 and are a starting point.
    """

    # Trimmed for speed in 0.5.1. Delays tied to real Boost work (panel populate
    # after selecting text, save->geometry conversion, close) stay conservative;
    # the pure UI-settle padding is cut. Bump an individual value back up if that
    # specific step ever flakes.
    after_screenshot: float = 0.3
    after_open: float = 1.0
    after_zoom: float = 0.7
    after_tool_activate: float = 0.4
    after_place_click: float = 0.7
    after_esc: float = 0.35
    after_panel_open: float = 1.0
    after_save: float = 1.2
    after_close: float = 1.2
    after_next_part: float = 0.4
    startup_countdown: float = 5.0


@dataclass
class CutConfig:
    """Cut-window (Qt/Qtitan ribbon) parameters.

    The Cut window's ribbon is a Qtitan control that exposes NO buttons to UIA
    (confirmed by probe: the RibbonBar node has no button children), so its
    tools are clicked by position within the maximized window. Offsets are from
    the Cut window's top-left, valid for the 1920-wide 'Start' ribbon layout;
    re-tune if the RDP resolution / ribbon layout changes.
    """

    # 'All : <machine>' auto-apply cutting-technology button on the Start tab.
    apply_button_offset: tuple = (291, 85)
    # After clicking auto-apply, how long the cutting data takes to compute
    # before the completion notice appears (observed 0.5-2.5s; wait a little
    # past the upper bound so slower parts still finish before we Esc/save).
    apply_wait_s: float = 3.0
    # Settle after Ctrl+S before Alt+F4 closes the Cut window.
    after_save_s: float = 1.0
    # Seconds to wait for the Cut window to close back to Home after Alt+F4.
    close_timeout_s: int = 10
    # Minimum Cut-window width (px) for the ribbon to be at its normal
    # left-anchored layout. Below this the ribbon groups may collapse and the
    # positional apply-click would be unreliable, so we refuse instead of miss.
    min_ribbon_width: int = 1000


@dataclass
class Config:
    """Top-level AutoBoost configuration."""

    # Image-template matching (used by the vision navigator fallback).
    images_dir: str = "images"
    image_confidence: float = 0.58
    more_button_confidence: float = 0.72
    font_confidence: float = 0.65

    # Text detection (locating the placed yellow part-number text).
    text_search_radius: int = 150
    zoom_steps: int = 5
    max_zoom_attempts: int = 6

    # Orchestration / safety.
    max_part_retries: int = 2
    max_consecutive_failures: int = 5
    undo_count: int = 20

    # Nested config blocks.
    canvas: CanvasRegion = field(default_factory=CanvasRegion)
    placement: PlacementConfig = field(default_factory=PlacementConfig)
    timing: TimingConfig = field(default_factory=TimingConfig)
    cut: CutConfig = field(default_factory=CutConfig)

    def to_json(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)

    @classmethod
    def from_json(cls, path: str) -> "Config":
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        canvas = CanvasRegion(**data.pop("canvas", {}))
        placement = PlacementConfig(**data.pop("placement", {}))
        timing = TimingConfig(**data.pop("timing", {}))
        cut_data = data.pop("cut", {})
        if "apply_button_offset" in cut_data:
            cut_data["apply_button_offset"] = tuple(cut_data["apply_button_offset"])
        cut = CutConfig(**cut_data)
        return cls(canvas=canvas, placement=placement, timing=timing, cut=cut, **data)


DEFAULT = Config()
