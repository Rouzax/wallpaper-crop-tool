"""
Export worker function for parallel image processing (Qt-free).

This module is imported in child processes spawned by
``concurrent.futures.ProcessPoolExecutor``.  It must **never** import
PyQt6 — doing so can crash or hang on some platforms.
"""

from pathlib import Path

from PIL import Image

from wallpaper_crop_tool.models import calculate_max_crop
from wallpaper_crop_tool.image_io import open_image, unique_path
from wallpaper_crop_tool.logo import composite_logo


def process_worker(args: dict) -> dict:
    """Worker function for parallel image processing. Runs in a separate process."""
    idx = args["index"]
    img_path = Path(args["path"])
    img_w = args["img_w"]
    img_h = args["img_h"]
    crops = args["crops"]  # {ratio_name: (x, y, w, h)}
    ratios = args["ratios"]
    output_root = Path(args["output_root"])
    rel_parent = args["rel_parent"]  # str or None
    export = args.get("export", {})
    logo_settings = args.get("logo")  # None or dict with logo config

    # Export settings with backwards-compatible defaults
    fmt = export.get("format", "PNG")
    compress = export.get("compress_level", 9)
    jpeg_quality = export.get("jpeg_quality", 95)
    jpeg_subsampling = export.get("jpeg_subsampling", 0)
    jpeg_optimize = export.get("jpeg_optimize", True)

    try:
        img = open_image(img_path).convert("RGB")

        for r in ratios:
            crop = crops.get(r["name"])
            if not crop:
                cw, ch = calculate_max_crop(img_w, img_h, r["ratio_w"], r["ratio_h"])
                cx = (img_w - cw) // 2
                cy = (img_h - ch) // 2
                crop = (cx, cy, cw, ch)

            x, y, w, h = crop
            cropped = img.crop((x, y, x + w, y + h))
            resized = cropped.resize((r["target_w"], r["target_h"]), Image.Resampling.LANCZOS)

            # Apply logo overlay if enabled
            if logo_settings and logo_settings.get("enabled"):
                logo_path = Path(logo_settings["path"])
                resized = composite_logo(
                    resized, logo_path,
                    position=logo_settings["position"],
                    size_percent=logo_settings["size_percent"],
                    base_dimension=logo_settings["base_dimension"],
                    margin_auto=logo_settings.get("margin_auto", False),
                    margin_ratio=logo_settings.get("margin_ratio", 0.75),
                    margin_px=logo_settings.get("margin_px", 40),
                )

            out_dir = output_root / r["folder"]
            if rel_parent:
                out_dir = out_dir / rel_parent
            out_dir.mkdir(parents=True, exist_ok=True)

            if fmt == "JPEG":
                out_path = unique_path(out_dir / f"{img_path.stem}.jpg")
                resized.save(
                    str(out_path), "JPEG",
                    quality=jpeg_quality,
                    optimize=jpeg_optimize,
                    subsampling=jpeg_subsampling,
                )
            else:
                out_path = unique_path(out_dir / f"{img_path.stem}.png")
                resized.save(str(out_path), "PNG", compress_level=compress)

        return {"index": idx, "success": True, "name": img_path.name}
    except Exception as e:
        return {"index": idx, "success": False, "name": img_path.name, "error": str(e)}
