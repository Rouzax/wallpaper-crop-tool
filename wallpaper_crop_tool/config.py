"""
Application constants and configuration.

DEFAULT_RATIOS provides the built-in fallback ratios. Runtime ratios are
loaded from ratios.json via the ratios module. All other constants control
crop-editor behaviour, file handling, and logo-overlay defaults.

The ``config_dir()`` helper returns the platform-appropriate config
directory and is shared by all persistence modules (ratios, crop cache).
"""

import os
import subprocess
import sys
from pathlib import Path

# =============================================================================
# APP IDENTITY & CONFIG DIRECTORY
# =============================================================================
APP_NAME = "wallpaper-crop-tool"


def config_dir() -> Path:
    """Return the platform-appropriate config directory, creating it if needed."""
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    directory = base / APP_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory

# =============================================================================
# DEFAULT RATIOS — Built-in fallback when ratios.json is missing or corrupt
# =============================================================================
DEFAULT_RATIOS = [
    {
        "name": "16:9",
        "ratio_w": 16,
        "ratio_h": 9,
        "targets": [
            {"target_w": 3840, "target_h": 2160, "folder": "Ratio 16x9"},
        ],
    },
    {
        "name": "16:10",
        "ratio_w": 16,
        "ratio_h": 10,
        "targets": [
            {"target_w": 3840, "target_h": 2400, "folder": "Ratio 16x10"},
        ],
    },
    {
        "name": "12:5",
        "ratio_w": 12,
        "ratio_h": 5,
        "targets": [
            {"target_w": 3840, "target_h": 1600, "folder": "Ratio 12x5"},
        ],
    },
]

# PNG compression level (0-9, 9 = maximum compression)
PNG_COMPRESS_LEVEL = 9

# JPEG export defaults
JPEG_QUALITY_DEFAULT = 95
JPEG_QUALITY_MIN = 1
JPEG_QUALITY_MAX = 100
JPEG_SUBSAMPLING_OPTIONS = ["4:4:4", "4:2:2", "4:2:0"]
JPEG_SUBSAMPLING_DEFAULT = "4:4:4"

# Map subsampling labels to Pillow integer values
JPEG_SUBSAMPLING_MAP = {"4:4:4": 0, "4:2:2": 1, "4:2:0": 2}

# Output format options
OUTPUT_FORMATS = ["PNG", "JPEG"]
OUTPUT_FORMAT_DEFAULT = "PNG"

# ---------------------------------------------------------------------------
# ImageMagick availability detection
# ---------------------------------------------------------------------------
# v7 uses a single ``magick`` binary; v6 uses ``convert``/``identify`` etc.
HAS_MAGICK = False
MAGICK_VERSION = 0  # Major version (6 or 7)

for _cmd, _ver in [("magick", 7), ("convert", 6)]:
    try:
        _magick_check = subprocess.run(
            [_cmd, "--version"], capture_output=True, timeout=5,
        )
        if _magick_check.returncode == 0:
            HAS_MAGICK = True
            MAGICK_VERSION = _ver
            break
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Ghostscript availability detection (required for AI file rasterization)
# ---------------------------------------------------------------------------
HAS_GHOSTSCRIPT = False

# Windows ships gswin64c / gswin32c; Linux/macOS use gs
_gs_candidates = (["gswin64c", "gswin32c"] if sys.platform == "win32" else []) + ["gs"]
for _gs_cmd in _gs_candidates:
    try:
        _gs_check = subprocess.run(
            [_gs_cmd, "--version"], capture_output=True, timeout=5,
        )
        if _gs_check.returncode == 0:
            HAS_GHOSTSCRIPT = True
            break
    except Exception:
        pass


def magick_cmd(*args: str) -> list[str]:
    """Build an ImageMagick command line that works on both v6 and v7.

    Usage examples::

        magick_cmd("identify", "-format", "%w", "file.png")
        # v7 → ["magick", "identify", "-format", "%w", "file.png"]
        # v6 → ["identify", "-format", "%w", "file.png"]

        magick_cmd("-density", "72", "file.ai", "PNG:-")
        # v7 → ["magick", "-density", "72", "file.ai", "PNG:-"]
        # v6 → ["convert", "-density", "72", "file.ai", "PNG:-"]

    When the first arg is a known v6 subcommand (identify, composite, mogrify,
    montage, display, animate), it's kept as-is for v6 and prefixed with
    ``magick`` for v7.  Otherwise the args are treated as ``convert``/``magick``
    arguments.
    """
    _V6_SUBCOMMANDS = {"identify", "composite", "mogrify", "montage", "display", "animate"}
    args_list = list(args)
    if MAGICK_VERSION >= 7:
        return ["magick"] + args_list
    # v6: first arg may be a subcommand name, or implicit "convert"
    if args_list and args_list[0] in _V6_SUBCOMMANDS:
        return args_list  # e.g. ["identify", ...]
    return ["convert"] + args_list


# AI file rasterization constants
AI_RASTER_MIN_PIXELS = 3840   # Longest side for preview raster
AI_RASTER_MAX_DENSITY = 4800  # Safety cap for ImageMagick density

# Supported image extensions (AI requires ImageMagick + Ghostscript)
_IMAGE_EXTENSIONS_BASE = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".psd"}
_IMAGE_EXTENSIONS_MAGICK = {".ai"}
IMAGE_EXTENSIONS = _IMAGE_EXTENSIONS_BASE | (
    _IMAGE_EXTENSIONS_MAGICK if HAS_MAGICK and HAS_GHOSTSCRIPT else set()
)

# Nudge amounts (pixels in image coordinates)
NUDGE_SMALL = 1
NUDGE_LARGE = 10

# Minimum crop size (pixels)
MIN_CROP_SIZE = 50

# Handle size for resize corners (pixels in screen coordinates)
HANDLE_SIZE = 10

# Logo overlay settings
LOGO_POSITIONS = ["TopRight", "TopLeft", "BottomRight", "BottomLeft", "Center"]
LOGO_BASE_DIMENSIONS = ["Width", "Height", "Shorter side"]
