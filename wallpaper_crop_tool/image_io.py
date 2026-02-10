"""
Qt-free image I/O utilities.

Provides helpers to open images (including PSD), read dimensions without full
loading, compute content fingerprints, and generate unique file paths.  Safe
to import in worker processes.
"""

import hashlib
from pathlib import Path

from PIL import Image
from psd_tools import PSDImage

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
