"""
Qt-free image I/O utilities.

Provides helpers to open images (including PSD), read dimensions without full
loading, and generate unique file paths.  Safe to import in worker processes.
"""

from pathlib import Path

from PIL import Image
from psd_tools import PSDImage

# Allow very large images (Pillow's default limit is ~178MP)
Image.MAX_IMAGE_PIXELS = None


def open_image(path: Path) -> Image.Image:
    """Open an image file, using psd-tools for PSD files, Pillow for everything else."""
    if path.suffix.lower() == ".psd":
        psd = PSDImage.open(str(path))
        return psd.composite()
    return Image.open(path)


def get_image_size(path: Path) -> tuple[int, int]:
    """Get image dimensions without fully loading/compositing."""
    if path.suffix.lower() == ".psd":
        psd = PSDImage.open(str(path))
        return psd.width, psd.height
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
