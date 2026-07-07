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

    left: float = 0.20    # skip the left Properties/parts panel
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

    # Canny edge thresholds for extracting part geometry (line drawing).
    canny_low: int = 40
    canny_high: int = 120
    # Dilation to close small gaps in the outline so contours are watertight.
    close_kernel: int = 3
    close_iterations: int = 2
    # Ignore contours smaller than this many pixels (noise / tiny dimension marks).
    min_contour_area: int = 2000
    # Extra safety erosion of the valid body mask, in pixels, before the
    # distance transform. Accounts for line thickness and small render blur.
    body_erode_px: int = 3
    # Minimum clearance (radius, px) required at the chosen point for the
    # placement to be considered safe. This is the single most important knob
    # and MUST be calibrated from real screenshots once we know the on-screen
    # size of the ~3x expanded text. Placeholder until calibrated.
    required_clearance_px: int = 45


@dataclass
class TimingConfig:
    """Sleep durations (seconds) between UI actions.

    Screen-scraping needs the UI to settle between steps. These mirror the
    values that were found to work in BoostPY v0.01.20 and are a starting point.
    """

    after_screenshot: float = 0.5
    after_open: float = 1.5
    after_zoom: float = 1.5
    after_tool_activate: float = 0.6
    after_place_click: float = 1.2
    after_esc: float = 0.8
    after_panel_open: float = 1.5
    after_save: float = 1.5
    after_close: float = 2.0
    after_next_part: float = 0.7
    startup_countdown: float = 5.0


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
        return cls(canvas=canvas, placement=placement, timing=timing, **data)


DEFAULT = Config()
