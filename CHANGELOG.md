# Changelog

## 1.1.0 — 2026-02-06

### Added

- **Logo overlay**: optional SVG/PNG logo compositing with configurable position, size (% of width/height/shorter side), and margin — with live preview on the crop editor
- Logo size range extended to 100% (up from 50%)

### Changed

- Refactored single-file module (1,645 lines) into a modular package with 10 focused modules
- Worker processes no longer import PyQt6 — only Qt-free modules are loaded in child processes
- Split `_build_ui` into focused sub-builders for improved maintainability
- Run command changed from `python wallpaper_crop_tool.py` to `python -m wallpaper_crop_tool`
- Configuration constants moved to `wallpaper_crop_tool/config.py`

### Fixed

- Batch export progress dialog now appears immediately instead of freezing during worker startup

## 1.0.0 — 2026-02-06

### Features

- Interactive crop editor with draggable body and resizable corner handles (aspect ratio locked)
- Rule-of-thirds overlay for composition guidance
- Support for 3 default aspect ratios: 16:9, 16:10, 12:5 (easily configurable)
- PSD (Photoshop) file support via psd-tools with automatic layer flattening
- Recursive subfolder scanning with structure preservation in output
- Parallel batch export using multiple CPU cores
- Background image loading for responsive UI with large files
- Progress tracking: reviewed/exported counters per image
- Progress dialogs for folder scanning, single export, and batch export
- Keyboard shortcuts for image/ratio navigation and crop actions
- Auto-center-max crop initialization for all images
- Duplicate filename protection with automatic `-01`, `-02` suffixes
- Large image support (no pixel limit)
- Dark theme UI
