# Changelog

## 1.4.0 — 2026-02-10

### Added

- **Crop cache**: crop positions are remembered across application restarts
  - Crops survive rename, move, and rescan — images are identified by content fingerprint (SHA-256 of first 64 KB + file size), not file path
  - Cache file stored alongside `ratios.json` in the user config directory: `crop_cache.json`
  - Automatic restore on rescan: previously cropped images show their saved positions instead of auto-center defaults
  - Dimension validation: if an image is replaced with a different resolution at the same path, the stale cache entry is ignored
  - Graceful degradation: missing or corrupt cache file is silently treated as empty (auto-center-max for all images, same as before)
  - Cache is saved automatically on image navigation, ratio editing, and application close — no user action required

### Changed

- **Shared config directory**: `config_dir()` and `APP_NAME` extracted from `ratios.py` into `config.py` so multiple persistence modules (ratios, crop cache) share the same path logic

## 1.3.0 — 2026-02-09

### Added

- **Ratio grouping**: one crop per aspect ratio, shared across multiple export resolutions
  - A single crop position applies to all targets with the same aspect ratio — crop once, export at every resolution
  - Ratio buttons show `16:9 (×2)` when a group has multiple targets
  - Crop info panel shows `Exports to: 3840×2160, 1920×1080` for multi-target groups
- **Two-panel ratio editor**: replaces the flat table with a ratio groups (left) + targets (right) layout
  - Left panel: add, remove, and reorder ratio groups
  - Right panel: add and remove export targets for the selected group
  - "Add Ratio" dialog with duplicate aspect-ratio detection — `42:18` is blocked when `21:9` exists with a helpful message
  - "Add Target" dialog with auto-computed height from the group's aspect ratio
  - Removing the last target from a group auto-deletes the group
  - Live validation: red borders on invalid folders, duplicate folder detection across all groups
  - Reset to Defaults button restores the built-in 3 ratio groups
- **Normalized aspect detection**: `32:18` and `16:9` are recognized as the same aspect ratio internally via GCD reduction, while displaying the user's original input
- **External ratio config**: ratios are stored in `ratios.json` in the user config directory
  - Windows: `%APPDATA%/wallpaper-crop-tool/ratios.json`
  - macOS: `~/Library/Application Support/wallpaper-crop-tool/ratios.json`
  - Linux: `~/.config/wallpaper-crop-tool/ratios.json` (respects `$XDG_CONFIG_HOME`)
  - Auto-created with defaults on first launch
  - Automatic fallback to defaults if file is missing or corrupted
- ⚙️ "Edit Ratios…" button in the Aspect Ratios panel

### Changed

- **Nested JSON format** with versioned envelope: `{"version": 1, "ratios": [...]}` where each ratio group contains a `targets` array of export resolutions
- `RATIOS` in `config.py` renamed to `DEFAULT_RATIOS` (now a built-in fallback only)
- Ratio buttons are dynamically rebuilt when ratios change — no restart needed
- Crop positions are preserved when editing ratios: matching aspect keys keep their crops, new aspect ratios get auto-center-max, removed ratios' crops are discarded
- `ImageState.crops` is keyed by normalized aspect key (e.g. `"16:9"`) instead of ratio name
- Worker processes iterate groups → targets, cropping once per aspect ratio then resizing to each target

## 1.2.0 — 2026-02-06

### Added

- **JPEG export**: choose between PNG and JPEG output in the new Export Settings panel
  - Quality slider (1–100, default 95)
  - Chroma subsampling selector: 4:4:4 (no subsampling), 4:2:2, or 4:2:0
  - Huffman optimization enabled by default
- Export Settings group on right panel (between Actions and Logo Overlay)
- JPEG-specific controls auto-hide when PNG is selected

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
