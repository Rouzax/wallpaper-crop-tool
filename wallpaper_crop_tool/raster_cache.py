"""
Raster preview cache for AI files.

Caches rasterized AI previews as PNG files on disk so that reopening
an AI file or rescanning a folder is instant after the first rasterization.

Cache key is the content fingerprint from ``image_io.compute_fingerprint``.

This module is Qt-free and safe for worker import.
"""

import logging
from pathlib import Path

from PIL import Image

from wallpaper_crop_tool.config import config_dir

logger = logging.getLogger(__name__)

_CACHE_DIR_NAME = "raster_cache"


def cache_dir() -> Path:
    """Return the raster cache directory, creating it if needed."""
    d = config_dir() / _CACHE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def cache_path(fingerprint: str) -> Path:
    """Return the expected cache file path for a fingerprint."""
    return cache_dir() / f"{fingerprint}.png"


def get_cached_raster(fingerprint: str) -> Path | None:
    """Return the cached PNG path if it exists, or None."""
    if not fingerprint:
        return None
    p = cache_path(fingerprint)
    return p if p.is_file() else None


def store_raster(fingerprint: str, pil_image: Image.Image) -> None:
    """Save a PIL image as PNG to the raster cache."""
    if not fingerprint:
        return
    p = cache_path(fingerprint)
    try:
        pil_image.save(str(p), "PNG")
        logger.debug("Stored raster cache: %s", p)
    except OSError as exc:
        logger.warning("Failed to write raster cache %s: %s", p, exc)
