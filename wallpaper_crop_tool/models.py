"""
Data models and crop-geometry utilities.

CropRect and ImageState are the core data structures shared across the UI
and the export worker.  ``ImageState.crops`` is keyed by ``aspect_key()``
output (e.g. ``"16:9"``), so one crop is shared across all export targets
for a given aspect ratio.  The four helper functions handle aspect-ratio
math and boundary clamping.
"""

from dataclasses import dataclass, field
from pathlib import Path

from wallpaper_crop_tool.config import MIN_CROP_SIZE


# =============================================================================
# Data classes
# =============================================================================
@dataclass
class CropRect:
    """Crop rectangle in image coordinates."""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


@dataclass
class ImageState:
    """Tracks crop state for one image across all ratios."""
    path: Path = None
    rel_path: Path = None  # relative path from input root (including filename)
    img_w: int = 0
    img_h: int = 0
    crops: dict = field(default_factory=dict)  # aspect_key (e.g. "16:9") -> CropRect
    reviewed: bool = False   # user has visited this image
    processed: bool = False  # image has been exported


# =============================================================================
# Crop math utilities
# =============================================================================
def calculate_max_crop(img_w: int, img_h: int, ratio_w: int, ratio_h: int) -> tuple[int, int]:
    """Calculate the maximum crop dimensions for a given aspect ratio within an image."""
    aspect = ratio_w / ratio_h
    # Try full width
    crop_w = img_w
    crop_h = int(round(crop_w / aspect))
    if crop_h <= img_h:
        return crop_w, crop_h
    # Full height
    crop_h = img_h
    crop_w = int(round(crop_h * aspect))
    return min(crop_w, img_w), crop_h


def center_crop(img_w: int, img_h: int, crop_w: int, crop_h: int) -> CropRect:
    """Return a centered crop rectangle."""
    x = (img_w - crop_w) // 2
    y = (img_h - crop_h) // 2
    return CropRect(x, y, crop_w, crop_h)


def auto_center_max(img_w: int, img_h: int, ratio_w: int, ratio_h: int) -> CropRect:
    """Maximum crop, centered."""
    cw, ch = calculate_max_crop(img_w, img_h, ratio_w, ratio_h)
    return center_crop(img_w, img_h, cw, ch)


def clamp_crop(crop: CropRect, img_w: int, img_h: int) -> CropRect:
    """Clamp crop rectangle to image bounds."""
    w = max(MIN_CROP_SIZE, min(crop.w, img_w))
    h = max(MIN_CROP_SIZE, min(crop.h, img_h))
    x = max(0, min(crop.x, img_w - w))
    y = max(0, min(crop.y, img_h - h))
    return CropRect(x, y, w, h)
