"""
Application constants and configuration.

Edit RATIOS to add/remove screen ratios. All other constants control
crop-editor behaviour, file handling, and logo-overlay defaults.
"""

# =============================================================================
# RATIO CONFIGURATION — Edit this list to add/remove screen ratios
# =============================================================================
RATIOS = [
    {
        "name": "16:9",
        "ratio_w": 16,
        "ratio_h": 9,
        "target_w": 3840,
        "target_h": 2160,
        "folder": "Ratio 16x9",
    },
    {
        "name": "16:10",
        "ratio_w": 16,
        "ratio_h": 10,
        "target_w": 3840,
        "target_h": 2400,
        "folder": "Ratio 16x10",
    },
    {
        "name": "12:5",
        "ratio_w": 12,
        "ratio_h": 5,
        "target_w": 3840,
        "target_h": 1600,
        "folder": "Ratio 12x5",
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

# Supported image extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".psd"}

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
