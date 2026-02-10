"""
Persistent crop cache: remember crop positions across application restarts.

Images are identified by a content fingerprint (see ``image_io.compute_fingerprint``),
making the cache resilient to file renames and moves.  Image dimensions are
validated on restore to guard against file replacement.

The on-disk format uses a versioned envelope::

    {
        "version": 1,
        "images": {
            "<fingerprint>": {
                "img_w": 5120,
                "img_h": 2880,
                "last_used": "2026-02-10T14:30:00",
                "crops": {
                    "16:9": [0, 140, 5120, 2880],
                    "16:10": [128, 0, 4608, 2880]
                }
            }
        }
    }

This module is Qt-free and safe for worker import.
"""

import json
import logging
from datetime import datetime, timezone

from wallpaper_crop_tool.config import config_dir
from wallpaper_crop_tool.models import CropRect

logger = logging.getLogger(__name__)

_CACHE_FILENAME = "crop_cache.json"
_CACHE_VERSION = 1


# =============================================================================
# Serialization helpers
# =============================================================================
def _crop_to_list(crop: CropRect) -> list[int]:
    """Serialize a CropRect to a JSON-safe [x, y, w, h] list."""
    return [crop.x, crop.y, crop.w, crop.h]


def _list_to_crop(data: list) -> CropRect | None:
    """Deserialize a [x, y, w, h] list to a CropRect, or None if invalid."""
    if isinstance(data, list) and len(data) == 4 and all(isinstance(v, int) for v in data):
        return CropRect(*data)
    return None


# =============================================================================
# Load / Save
# =============================================================================
def load_crop_cache() -> dict:
    """
    Load the crop cache from disk.

    Returns the ``images`` dict from the versioned envelope, or an empty
    dict if the file is missing, corrupt, or has an unexpected version.
    """
    path = config_dir() / _CACHE_FILENAME

    if not path.exists():
        logger.debug("No crop cache found at %s — starting fresh", path)
        return {}

    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read crop cache (%s) — starting fresh", exc)
        return {}

    if not isinstance(raw, dict) or raw.get("version") != _CACHE_VERSION:
        logger.warning("Crop cache version mismatch or invalid format — starting fresh")
        return {}

    images = raw.get("images")
    if not isinstance(images, dict):
        logger.warning("Crop cache missing 'images' dict — starting fresh")
        return {}

    logger.info("Loaded crop cache with %d entries from %s", len(images), path)
    return images


def save_crop_cache(cache: dict) -> None:
    """
    Write the crop cache to disk in a versioned envelope.

    The *cache* argument should be the ``images`` dict (as returned by
    ``load_crop_cache``).
    """
    envelope = {"version": _CACHE_VERSION, "images": cache}
    path = config_dir() / _CACHE_FILENAME
    try:
        path.write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.debug("Saved crop cache (%d entries) to %s", len(cache), path)
    except OSError as exc:
        logger.error("Could not write crop cache to %s: %s", path, exc)


# =============================================================================
# Lookup / Store
# =============================================================================
def lookup_crops(cache: dict, fingerprint: str, img_w: int, img_h: int) -> dict[str, CropRect] | None:
    """
    Look up cached crops for an image by fingerprint.

    Returns a dict of ``{aspect_key: CropRect}`` if the fingerprint is
    found and the stored dimensions match *img_w* × *img_h*.  Returns
    ``None`` on miss, dimension mismatch, or invalid data.
    """
    entry = cache.get(fingerprint)
    if entry is None:
        return None

    # Validate dimensions — guard against a different file with same prefix hash
    if entry.get("img_w") != img_w or entry.get("img_h") != img_h:
        logger.debug(
            "Crop cache dimension mismatch for %s: cached %sx%s, actual %sx%s — ignoring",
            fingerprint, entry.get("img_w"), entry.get("img_h"), img_w, img_h,
        )
        return None

    raw_crops = entry.get("crops")
    if not isinstance(raw_crops, dict):
        return None

    # Deserialize each crop, skipping any that are malformed
    crops: dict[str, CropRect] = {}
    for akey, data in raw_crops.items():
        crop = _list_to_crop(data)
        if crop is not None:
            crops[akey] = crop

    return crops if crops else None


def store_crops(
    cache: dict,
    fingerprint: str,
    img_w: int,
    img_h: int,
    crops: dict[str, CropRect],
) -> None:
    """
    Upsert crop data for an image into the in-memory cache.

    *crops* should be a dict of ``{aspect_key: CropRect}``.  A
    ``last_used`` ISO timestamp is recorded for future eviction use.
    """
    cache[fingerprint] = {
        "img_w": img_w,
        "img_h": img_h,
        "last_used": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "crops": {akey: _crop_to_list(crop) for akey, crop in crops.items()},
    }
