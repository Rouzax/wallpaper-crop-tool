"""
Ratios persistence: load, save, and validate ratio configuration.

Runtime ratios are stored in a JSON file in the user's config directory
(provided by ``config.config_dir()``).  On first launch (or if the file
is missing/corrupt), the file is created from DEFAULT_RATIOS.  This
module is Qt-free and safe for worker import.

The on-disk format uses a versioned envelope::

    {"version": 1, "ratios": [ ... ]}

Each ratio group has a ``targets`` list containing one or more export
targets.  Crops are keyed by ``aspect_key()`` — one crop per unique
normalized aspect ratio.
"""

import json
import logging
import os
from copy import deepcopy
from math import gcd
from pathlib import Path

from wallpaper_crop_tool.config import DEFAULT_RATIOS, config_dir

logger = logging.getLogger(__name__)

_RATIOS_FILENAME = "ratios.json"
_FORMAT_VERSION = 1

_GROUP_REQUIRED_KEYS = {"name", "ratio_w", "ratio_h", "targets"}
_GROUP_INT_KEYS = ("ratio_w", "ratio_h")
_TARGET_REQUIRED_KEYS = {"target_w", "target_h", "folder"}
_TARGET_INT_KEYS = ("target_w", "target_h")

# Characters forbidden in folder names (superset across Windows/macOS/Linux)
_INVALID_FOLDER_CHARS = set('<>:"|?*\\\0')
# Reserved device names on Windows (case-insensitive)
_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


# =============================================================================
# Aspect-ratio helpers
# =============================================================================
def normalize_ratio(w: int, h: int) -> tuple[int, int]:
    """Reduce ratio to simplest form via GCD. (21, 9) → (7, 3)"""
    g = gcd(w, h)
    return w // g, h // g


def aspect_key(w: int, h: int) -> str:
    """Normalized string key for crop dicts. (21, 9) → '7:3'"""
    nw, nh = normalize_ratio(w, h)
    return f"{nw}:{nh}"


# =============================================================================
# Config directory helpers
# =============================================================================
def _ratios_path() -> Path:
    """Return the full path to ratios.json."""
    return config_dir() / _RATIOS_FILENAME


# =============================================================================
# Validation
# =============================================================================
def validate_folder_name(folder: str) -> str | None:
    """
    Validate a folder name for safe use as a single directory component.

    Returns an error string if invalid, or None if valid.
    """
    if not isinstance(folder, str) or not folder.strip():
        return "folder must be a non-empty string"

    # Leading/trailing dots or spaces (problematic on Windows)
    if folder != folder.strip() or folder.startswith(".") or folder.endswith("."):
        return "folder must not start or end with a dot or space"

    folder = folder.strip()

    # Path traversal
    if ".." in folder or "/" in folder or "\\" in folder:
        return "folder must not contain path separators or '..'"

    # Absolute paths
    if os.path.isabs(folder):
        return "folder must not be an absolute path"

    # Invalid characters
    bad = _INVALID_FOLDER_CHARS & set(folder)
    if bad:
        return f"folder contains invalid characters: {' '.join(sorted(repr(c) for c in bad))}"

    # Reserved Windows device names (e.g. CON, NUL, COM1)
    stem = folder.split(".")[0].upper()
    if stem in _RESERVED_NAMES:
        return f"folder uses reserved name '{stem}'"

    return None


def validate_ratios(data: object) -> list[str]:
    """
    Validate a ratios data structure (nested format with targets).

    Returns a list of error strings (empty means valid).
    """
    errors: list[str] = []

    if not isinstance(data, list):
        errors.append("Ratios data must be a list")
        return errors

    aspect_keys_seen: dict[str, str] = {}  # aspect_key -> group name
    folders_seen: dict[str, str] = {}       # folder -> "Group 'name' target #N"

    for i, group in enumerate(data):
        prefix = f"Ratio #{i + 1}"

        if not isinstance(group, dict):
            errors.append(f"{prefix}: must be a dict")
            continue

        # Check required group-level keys
        missing = _GROUP_REQUIRED_KEYS - group.keys()
        if missing:
            errors.append(f"{prefix}: missing keys: {', '.join(sorted(missing))}")
            continue

        # Check name
        name = group.get("name", "")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{prefix}: name must be a non-empty string")

        # Check integer fields are positive integers
        for key in _GROUP_INT_KEYS:
            val = group.get(key)
            if not isinstance(val, int) or val <= 0:
                errors.append(f"{prefix}: {key} must be a positive integer, got {val!r}")

        # Check for duplicate normalized aspect ratios across groups
        ratio_w = group.get("ratio_w", 0)
        ratio_h = group.get("ratio_h", 0)
        if isinstance(ratio_w, int) and isinstance(ratio_h, int) and ratio_w > 0 and ratio_h > 0:
            akey = aspect_key(ratio_w, ratio_h)
            if akey in aspect_keys_seen:
                errors.append(
                    f"{prefix} ('{name}'): normalized aspect ratio {akey} "
                    f"duplicates group '{aspect_keys_seen[akey]}'"
                )
            else:
                aspect_keys_seen[akey] = name

        # Validate targets list
        targets = group.get("targets")
        if not isinstance(targets, list) or len(targets) == 0:
            errors.append(f"{prefix}: targets must be a non-empty list")
            continue

        for j, target in enumerate(targets):
            tprefix = f"{prefix} target #{j + 1}"

            if not isinstance(target, dict):
                errors.append(f"{tprefix}: must be a dict")
                continue

            # Check required target keys
            tmissing = _TARGET_REQUIRED_KEYS - target.keys()
            if tmissing:
                errors.append(f"{tprefix}: missing keys: {', '.join(sorted(tmissing))}")
                continue

            # Check target integer fields
            for key in _TARGET_INT_KEYS:
                val = target.get(key)
                if not isinstance(val, int) or val <= 0:
                    errors.append(f"{tprefix}: {key} must be a positive integer, got {val!r}")

            # Validate folder
            folder = target.get("folder", "")
            folder_err = validate_folder_name(folder)
            if folder_err:
                errors.append(f"{tprefix}: {folder_err}")

            # Check for duplicate folders across all groups/targets
            if isinstance(folder, str) and folder.strip():
                folder_key = folder.strip()
                location = f"'{name}' target #{j + 1}"
                if folder_key in folders_seen:
                    errors.append(
                        f"{tprefix}: duplicate folder '{folder_key}' "
                        f"(also used by {folders_seen[folder_key]})"
                    )
                else:
                    folders_seen[folder_key] = location

    return errors


# =============================================================================
# Load / Save
# =============================================================================
def load_ratios() -> list[dict]:
    """
    Load ratios from ratios.json.

    If the file is missing, corrupt, or fails validation, writes the
    defaults and returns them.  The on-disk format is a versioned
    envelope: ``{"version": 1, "ratios": [...]}``.  This function
    extracts and returns just the ``ratios`` list.
    """
    path = _ratios_path()

    if not path.exists():
        logger.info("ratios.json not found — creating with defaults at %s", path)
        _write_defaults(path)
        return deepcopy(DEFAULT_RATIOS)

    try:
        text = path.read_text(encoding="utf-8")
        raw = json.loads(text)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read ratios.json (%s) — restoring defaults", exc)
        _write_defaults(path)
        return deepcopy(DEFAULT_RATIOS)

    # Extract ratios list from version envelope
    if not isinstance(raw, dict) or "version" not in raw or "ratios" not in raw:
        logger.warning("ratios.json missing version envelope — restoring defaults")
        _write_defaults(path)
        return deepcopy(DEFAULT_RATIOS)

    data = raw["ratios"]
    errors = validate_ratios(data)
    if errors:
        logger.warning(
            "ratios.json validation failed:\n  %s\nRestoring defaults.",
            "\n  ".join(errors),
        )
        _write_defaults(path)
        return deepcopy(DEFAULT_RATIOS)

    return data


def save_ratios(ratios: list[dict]) -> None:
    """
    Validate and write ratios to ratios.json in versioned envelope.

    Raises ValueError if validation fails.
    Raises OSError if the file cannot be written.
    """
    errors = validate_ratios(ratios)
    if errors:
        raise ValueError("Invalid ratios data:\n  " + "\n  ".join(errors))

    envelope = {"version": _FORMAT_VERSION, "ratios": ratios}
    path = _ratios_path()
    path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("Saved %d ratio group(s) to %s", len(ratios), path)


def _write_defaults(path: Path) -> None:
    """Write DEFAULT_RATIOS to the given path in versioned envelope."""
    try:
        envelope = {"version": _FORMAT_VERSION, "ratios": deepcopy(DEFAULT_RATIOS)}
        path.write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.error("Could not write default ratios to %s: %s", path, exc)
