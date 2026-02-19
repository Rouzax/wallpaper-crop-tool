"""
Qt-free image I/O utilities.

Provides helpers to open images (including PSD and AI), read dimensions without
full loading, compute content fingerprints, and generate unique file paths.
Safe to import in worker processes.
"""

import hashlib
import io
import subprocess
from pathlib import Path

from PIL import Image
from psd_tools import PSDImage

from wallpaper_crop_tool.config import AI_RASTER_MIN_PIXELS, AI_RASTER_MAX_DENSITY, magick_cmd

# Allow very large images (Pillow's default limit is ~178MP)
Image.MAX_IMAGE_PIXELS = None

# Number of bytes read for fingerprinting (64 KB)
_FINGERPRINT_READ_SIZE = 65_536


def compute_fingerprint(path: Path) -> str:
    """
    Compute a fast content fingerprint for an image file.

    Reads the first 64 KB of the file and combines it with the file size
    to produce a truncated SHA-256 hex string.  Format: ``"{size_hex}_{hash16}"``.

    This identifies files by content rather than path, so renamed or moved
    files produce the same fingerprint.  Different files (even with the
    same first 64 KB) are distinguished by file size.
    """
    size = path.stat().st_size
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        sha.update(f.read(_FINGERPRINT_READ_SIZE))
    return f"{size:x}_{sha.hexdigest()[:16]}"


def _probe_ai_points(path: Path) -> tuple[int, int]:
    """Probe an AI file's base point dimensions at 72 DPI."""
    result = subprocess.run(
        magick_cmd("identify", "-density", "72", "-format", "%w %h", f"{path}[0]"),
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ImageMagick identify failed: {result.stderr.strip()}")
    parts = result.stdout.strip().split()
    return int(parts[0]), int(parts[1])


def _ai_preview_density(w72: int, h72: int) -> int:
    """Calculate the density needed to rasterize an AI file at preview resolution.

    Targets ``AI_RASTER_MIN_PIXELS`` on the longest side, capped at
    ``AI_RASTER_MAX_DENSITY``.
    """
    longest = max(w72, h72)
    if longest <= 0:
        return 72
    density = max(72, round(72 * AI_RASTER_MIN_PIXELS / longest))
    return min(density, AI_RASTER_MAX_DENSITY)


def _rasterize_ai(path: Path) -> Image.Image:
    """Rasterize an AI file to a PIL Image at preview resolution."""
    w72, h72 = _probe_ai_points(path)
    density = _ai_preview_density(w72, h72)
    result = subprocess.run(
        magick_cmd("-density", str(density), "-background", "white",
                   f"{path}[0]", "-flatten", "PNG:-"),
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ImageMagick rasterize failed: {result.stderr.decode(errors='replace')}")
    return Image.open(io.BytesIO(result.stdout))


def _get_ai_size(path: Path) -> tuple[int, int]:
    """Get the rasterized dimensions of an AI file at preview density (fast, no full raster)."""
    w72, h72 = _probe_ai_points(path)
    density = _ai_preview_density(w72, h72)
    result = subprocess.run(
        magick_cmd("identify", "-density", str(density), "-format", "%w %h", f"{path}[0]"),
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ImageMagick identify failed: {result.stderr.strip()}")
    parts = result.stdout.strip().split()
    return int(parts[0]), int(parts[1])


def rasterize_ai_cropped(
    path: Path,
    crop: tuple[int, int, int, int],
    target_w: int, target_h: int,
    preview_w: int, preview_h: int,
) -> Image.Image:
    """Rasterize an AI file and crop at the optimal density for the target resolution.

    Parameters
    ----------
    path : Path
        Path to the AI file.
    crop : tuple
        ``(x, y, w, h)`` crop rectangle in preview-pixel coordinates.
    target_w, target_h : int
        Desired output resolution.
    preview_w, preview_h : int
        Dimensions of the preview raster (used to relate crop coordinates to density).
    """
    x, y, w, h = crop
    w72, h72 = _probe_ai_points(path)
    preview_density = _ai_preview_density(w72, h72)

    # Scale density so the crop region maps to the target resolution
    export_density = round(preview_density * target_w / w)
    needs_resize = False
    if export_density > AI_RASTER_MAX_DENSITY:
        export_density = AI_RASTER_MAX_DENSITY
        needs_resize = True

    result = subprocess.run(
        magick_cmd("-density", str(export_density), "-background", "white",
                   f"{path}[0]", "-flatten", "PNG:-"),
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ImageMagick rasterize failed: {result.stderr.decode(errors='replace')}")

    img = Image.open(io.BytesIO(result.stdout))

    # Scale crop coordinates from preview space to export space
    scale = export_density / preview_density
    sx = round(x * scale)
    sy = round(y * scale)
    sw = round(w * scale)
    sh = round(h * scale)

    # Clamp to rasterized image bounds
    sx = min(sx, img.width - 1)
    sy = min(sy, img.height - 1)
    sw = min(sw, img.width - sx)
    sh = min(sh, img.height - sy)

    cropped = img.crop((sx, sy, sx + sw, sy + sh))

    if needs_resize or (cropped.width != target_w or cropped.height != target_h):
        cropped = cropped.resize((target_w, target_h), Image.Resampling.LANCZOS)

    return cropped


def open_image(path: Path) -> Image.Image:
    """Open an image file, using psd-tools for PSD, ImageMagick for AI, Pillow for the rest."""
    ext = path.suffix.lower()
    if ext == ".psd":
        psd = PSDImage.open(str(path))
        return psd.composite()
    if ext == ".ai":
        return _rasterize_ai(path)
    return Image.open(path)


def get_image_size(path: Path) -> tuple[int, int]:
    """Get image dimensions without fully loading/compositing."""
    ext = path.suffix.lower()
    if ext == ".psd":
        psd = PSDImage.open(str(path))
        return psd.width, psd.height
    if ext == ".ai":
        return _get_ai_size(path)
    with Image.open(path) as img:
        return img.size


def unique_path(out_path: Path) -> Path:
    """Return a unique path by appending -01, -02, etc. if file already exists."""
    if not out_path.exists():
        return out_path
    stem = out_path.stem
    suffix = out_path.suffix
    parent = out_path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}-{counter:02d}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
