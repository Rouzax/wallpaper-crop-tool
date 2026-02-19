"""
Logo rasterization and compositing (Qt-free).

Handles SVG rasterization via ImageMagick and PNG logo resizing,
plus alpha-composite placement on export images.  Safe to import
in worker processes.
"""

import io
import subprocess
from pathlib import Path

from PIL import Image

from wallpaper_crop_tool.config import HAS_MAGICK, magick_cmd


def rasterize_logo(logo_path: Path, target_width: int) -> Image.Image:
    """Rasterize a logo (SVG or image) to a specific pixel width, preserving aspect ratio."""
    ext = logo_path.suffix.lower()
    if ext == ".svg":
        if not HAS_MAGICK:
            raise RuntimeError(
                "ImageMagick is required for SVG logos.\n"
                "Install from: https://imagemagick.org/\n"
                "Alternatively, use a PNG logo."
            )
        # Probe SVG dimensions at 72 DPI to calculate exact render density
        probe = subprocess.run(
            magick_cmd("-density", "72", "-background", "none", str(logo_path), "-format", "%w", "info:"),
            capture_output=True, text=True,
        )
        svg_w_72 = int(probe.stdout.strip()) if probe.returncode == 0 and probe.stdout.strip().isdigit() else 0

        if svg_w_72 > 0:
            # Exact density: render SVG directly at target width (no bitmap resize)
            exact_density = max(1, round(72.0 * target_width / svg_w_72))
            result = subprocess.run(
                magick_cmd("-density", str(exact_density), "-background", "none", str(logo_path), "PNG:-"),
                capture_output=True,
            )
        else:
            # Fallback: render at high density then resize
            result = subprocess.run(
                magick_cmd("-density", "300", "-background", "none", str(logo_path),
                           "-resize", f"{target_width}x", "PNG:-"),
                capture_output=True,
            )

        if result.returncode != 0:
            raise RuntimeError(f"ImageMagick failed: {result.stderr.decode(errors='replace')}")
        return Image.open(io.BytesIO(result.stdout)).convert("RGBA")
    else:
        img = Image.open(logo_path).convert("RGBA")
        if img.width == 0:
            return img
        aspect = img.height / img.width
        target_height = max(1, int(round(target_width * aspect)))
        return img.resize((target_width, target_height), Image.Resampling.LANCZOS)


def composite_logo(
    base: Image.Image, logo_path: Path, position: str,
    size_percent: float, base_dimension: str,
    margin_auto: bool = False, margin_ratio: float = 0.75, margin_px: int = 40,
) -> Image.Image:
    """Composite a logo onto a base image."""
    bw, bh = base.size

    # Calculate logo target width
    if base_dimension == "Width":
        basis = bw
    elif base_dimension == "Height":
        basis = bh
    else:  # Shorter side
        basis = min(bw, bh)
    logo_target_w = max(1, int(round(basis * size_percent / 100.0)))

    # Rasterize logo at exact target size
    logo = rasterize_logo(logo_path, logo_target_w)
    lw, lh = logo.size

    # Calculate margin
    if margin_auto:
        margin = max(1, int(round(lh * margin_ratio)))
    else:
        margin = margin_px

    # Calculate position
    if position == "TopLeft":
        x, y = margin, margin
    elif position == "TopRight":
        x, y = bw - lw - margin, margin
    elif position == "BottomLeft":
        x, y = margin, bh - lh - margin
    elif position == "BottomRight":
        x, y = bw - lw - margin, bh - lh - margin
    else:  # Center
        x, y = (bw - lw) // 2, (bh - lh) // 2

    # Clamp to image bounds
    x = max(0, min(x, bw - lw))
    y = max(0, min(y, bh - lh))

    # Composite with alpha
    result = base.convert("RGBA")
    result.paste(logo, (x, y), logo)
    return result.convert("RGB")
