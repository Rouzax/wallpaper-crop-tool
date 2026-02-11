"""
Main application window.

Orchestrates image loading, crop editing, ratio selection, logo overlay
configuration, and batch export via parallel workers.
"""

import os
import subprocess
import time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

from PIL import Image
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QFileDialog,
    QSplitter, QGroupBox, QMessageBox, QProgressDialog, QStatusBar,
    QToolBar, QCheckBox, QComboBox, QSpinBox, QSlider, QApplication,
    QScrollArea,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QImage, QAction, QKeySequence, QShortcut

from wallpaper_crop_tool.config import (
    PNG_COMPRESS_LEVEL, IMAGE_EXTENSIONS,
    LOGO_POSITIONS, LOGO_BASE_DIMENSIONS,
    OUTPUT_FORMATS, OUTPUT_FORMAT_DEFAULT,
    JPEG_QUALITY_DEFAULT, JPEG_QUALITY_MIN, JPEG_QUALITY_MAX,
    JPEG_SUBSAMPLING_OPTIONS, JPEG_SUBSAMPLING_DEFAULT, JPEG_SUBSAMPLING_MAP,
)
from wallpaper_crop_tool.ratios import load_ratios, save_ratios, aspect_key
from wallpaper_crop_tool.ratio_editor import RatioEditorDialog
from wallpaper_crop_tool.models import ImageState, auto_center_max
from wallpaper_crop_tool.image_io import open_image, get_image_size, unique_path, compute_fingerprint
from wallpaper_crop_tool.crop_cache import load_crop_cache, save_crop_cache, lookup_crops, store_crops
from wallpaper_crop_tool.logo import HAS_MAGICK, composite_logo
from wallpaper_crop_tool.worker import process_worker
from wallpaper_crop_tool.crop_widget import ImageCropWidget, ImageLoaderThread


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wallpaper Batch Crop Tool")
        self.setMinimumSize(900, 500)

        # Screen-aware startup size: default 1280×800, clamped to 80% of screen
        preferred_w, preferred_h = 1920, 1080
        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            max_w = int(avail.width() * 0.8)
            max_h = int(avail.height() * 0.8)
            preferred_w = min(preferred_w, max_w)
            preferred_h = min(preferred_h, max_h)
        self.resize(preferred_w, preferred_h)

        self._image_states: list[ImageState] = []
        self._current_index = -1
        self._current_ratio_idx = 0
        self._ratios = load_ratios()
        self._output_root: Path | None = None
        self._input_folder: Path | None = None
        self._loader: ImageLoaderThread | None = None
        self._crop_cache: dict = load_crop_cache()

        # Logo overlay state
        self._logo_path: Path | None = None
        self._logo_pixmap: QPixmap | None = None  # Full-resolution for preview

        self._build_ui()
        self._update_button_states()

    # =========================================================================
    # UI construction
    # =========================================================================

    def _build_ui(self):
        self._build_toolbar()

        # --- Central layout ---
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        splitter.addWidget(self._build_left_panel())

        # Center panel — crop editor
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)
        self._crop_widget = ImageCropWidget()
        self._crop_widget.crop_changed.connect(self._on_crop_changed)
        center_layout.addWidget(self._crop_widget, stretch=1)
        splitter.addWidget(center_panel)

        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([200, 800, 220])

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        magick_status = "SVG logos: ✓ ImageMagick found" if HAS_MAGICK else "SVG logos: ✗ ImageMagick not found (PNG logos OK)"
        self._status.showMessage(f"Select an input folder to begin.  |  {magick_status}")

        # --- Keyboard Shortcuts ---
        QShortcut(QKeySequence(Qt.Key.Key_PageDown), self, self._next_image)
        QShortcut(QKeySequence(Qt.Key.Key_D), self, self._next_image)
        QShortcut(QKeySequence(Qt.Key.Key_PageUp), self, self._prev_image)
        QShortcut(QKeySequence(Qt.Key.Key_A), self, self._prev_image)
        QShortcut(QKeySequence(Qt.Key.Key_Tab), self, self._next_ratio)
        QShortcut(QKeySequence(Qt.Key.Key_W), self, self._next_ratio)
        QShortcut(QKeySequence(Qt.KeyboardModifier.ShiftModifier | Qt.Key.Key_Tab), self, self._prev_ratio)
        QShortcut(QKeySequence(Qt.Key.Key_Q), self, self._prev_ratio)
        QShortcut(QKeySequence(Qt.Key.Key_C), self, self._auto_center_current)
        QShortcut(QKeySequence(Qt.KeyboardModifier.ShiftModifier | Qt.Key.Key_C), self, self._auto_center_all_ratios)

    def _build_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        act_input = QAction("📂 Open Input Folder", self)
        act_input.triggered.connect(self._select_input_folder)
        toolbar.addAction(act_input)

        act_output = QAction("💾 Set Output Folder", self)
        act_output.triggered.connect(self._select_output_folder)
        toolbar.addAction(act_output)

        toolbar.addSeparator()

        self._scan_subfolders = QCheckBox("Scan Subfolders")
        self._scan_subfolders.setChecked(True)
        self._scan_subfolders.setToolTip("Recursively scan subfolders and recreate structure in output")
        self._scan_subfolders.setStyleSheet("QCheckBox { padding: 4px 8px; }")
        toolbar.addWidget(self._scan_subfolders)

        toolbar.addSeparator()

        act_process_current = QAction("▶ Export Current Image", self)
        act_process_current.triggered.connect(self._process_current)
        toolbar.addAction(act_process_current)
        self._act_process_current = act_process_current

        act_process_all_manual = QAction("▶▶ Export All", self)
        act_process_all_manual.setToolTip("Export all images using your set crops (auto-centered by default)")
        act_process_all_manual.triggered.connect(self._process_all_manual)
        toolbar.addAction(act_process_all_manual)
        self._act_process_all_manual = act_process_all_manual

    def _build_left_panel(self) -> QWidget:
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("Images:"))
        self._image_list = QListWidget()
        self._image_list.currentRowChanged.connect(self._on_image_selected)
        left_layout.addWidget(self._image_list)

        self._counter_label = QLabel("")
        self._counter_label.setStyleSheet("color: #aaa; font-size: 8pt; padding: 2px;")
        left_layout.addWidget(self._counter_label)

        return left_panel

    def _build_right_panel(self) -> QWidget:
        # Inner widget holds all the controls
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)

        inner_layout.addWidget(self._build_ratio_group())

        # Crop info
        self._crop_info_label = QLabel("Crop: —")
        self._crop_info_label.setWordWrap(True)
        inner_layout.addWidget(self._crop_info_label)

        inner_layout.addWidget(self._build_actions_group())
        inner_layout.addWidget(self._build_export_group())
        inner_layout.addWidget(self._build_logo_group())
        inner_layout.addWidget(self._build_shortcuts_group())

        inner_layout.addStretch()

        # Scroll area wraps the inner widget so the panel can shrink
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setFrameShape(scroll.Shape.NoFrame)

        right_panel = QWidget()
        right_panel.setFixedWidth(240)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)
        right_layout.addWidget(scroll)
        return right_panel

    def _build_ratio_group(self) -> QGroupBox:
        ratio_group = QGroupBox("Aspect Ratios")
        self._ratio_layout = QVBoxLayout(ratio_group)
        self._ratio_buttons: list[QPushButton] = []
        self._rebuild_ratio_buttons()

        # Editor button — always at the bottom of the group
        btn_edit = QPushButton("⚙️ Edit Ratios…")
        btn_edit.clicked.connect(self._open_ratio_editor)
        self._ratio_layout.addWidget(btn_edit)

        return ratio_group

    def _rebuild_ratio_buttons(self):
        """Clear and recreate ratio buttons from self._ratios."""
        # Remove existing ratio buttons
        for btn in self._ratio_buttons:
            self._ratio_layout.removeWidget(btn)
            btn.deleteLater()
        self._ratio_buttons.clear()

        # Insert new buttons (before the ⚙️ button at the end)
        for i, r in enumerate(self._ratios):
            n_targets = len(r.get("targets", []))
            label = r["name"] if n_targets <= 1 else f"{r['name']} (×{n_targets})"
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=i: self._on_ratio_selected(idx))
            self._ratio_layout.insertWidget(i, btn)
            self._ratio_buttons.append(btn)

        # Select first ratio
        self._current_ratio_idx = 0
        if self._ratio_buttons:
            self._ratio_buttons[0].setChecked(True)

    def _open_ratio_editor(self):
        """Open the ratio editor dialog and apply changes on accept."""
        # Save current crop before anything changes
        self._save_current_crop()

        dlg = RatioEditorDialog(self._ratios, parent=self)
        if dlg.exec() != RatioEditorDialog.DialogCode.Accepted:
            return

        new_ratios = dlg.get_ratios()

        # Persist to JSON
        try:
            save_ratios(new_ratios)
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save ratios:\n{exc}")
            return

        # Build set of aspect keys for the new ratios
        new_aspect_keys: dict[str, dict] = {}
        for r in new_ratios:
            akey = aspect_key(r["ratio_w"], r["ratio_h"])
            new_aspect_keys[akey] = r

        # Reconcile crops for all loaded images
        for state in self._image_states:
            # Keep crops whose aspect key still exists, discard removed ones
            state.crops = {
                akey: crop for akey, crop in state.crops.items()
                if akey in new_aspect_keys
            }
            # Add auto-center-max for new aspect keys
            for akey, r in new_aspect_keys.items():
                if akey not in state.crops:
                    state.crops[akey] = auto_center_max(
                        state.img_w, state.img_h,
                        r["ratio_w"], r["ratio_h"],
                    )
            # Update cache with reconciled crops
            if state.fingerprint:
                store_crops(self._crop_cache, state.fingerprint,
                            state.img_w, state.img_h, state.crops)
        self._save_cache()

        # Update instance state and rebuild UI
        self._ratios = new_ratios
        self._rebuild_ratio_buttons()
        self._update_button_states()

        # Re-apply first ratio to the displayed image
        if self._ratios and self._current_index >= 0:
            self._apply_ratio(0)

    def _build_actions_group(self) -> QGroupBox:
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)

        btn_auto_center = QPushButton("🎯 Auto Center (Max)")
        btn_auto_center.setToolTip("Reset crop to maximum size, centered")
        btn_auto_center.clicked.connect(self._auto_center_current)
        actions_layout.addWidget(btn_auto_center)

        btn_auto_all_ratios = QPushButton("🎯 Auto Center All Ratios")
        btn_auto_all_ratios.setToolTip("Reset all ratio crops for this image")
        btn_auto_all_ratios.clicked.connect(self._auto_center_all_ratios)
        actions_layout.addWidget(btn_auto_all_ratios)

        actions_layout.addSpacing(10)

        btn_prev = QPushButton("← Previous Image")
        btn_prev.clicked.connect(self._prev_image)
        actions_layout.addWidget(btn_prev)
        self._btn_prev = btn_prev

        btn_next = QPushButton("→ Next Image")
        btn_next.clicked.connect(self._next_image)
        actions_layout.addWidget(btn_next)
        self._btn_next = btn_next

        return actions_group

    def _build_export_group(self) -> QGroupBox:
        export_group = QGroupBox("Export Settings")
        export_layout = QVBoxLayout(export_group)

        # Format dropdown
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._export_format = QComboBox()
        self._export_format.addItems(OUTPUT_FORMATS)
        self._export_format.setCurrentText(OUTPUT_FORMAT_DEFAULT)
        self._export_format.currentTextChanged.connect(self._on_export_format_changed)
        fmt_row.addWidget(self._export_format)
        export_layout.addLayout(fmt_row)

        # JPEG quality slider
        quality_row = QHBoxLayout()
        quality_row.addWidget(QLabel("Quality:"))
        self._jpeg_quality_slider = QSlider(Qt.Orientation.Horizontal)
        self._jpeg_quality_slider.setRange(JPEG_QUALITY_MIN, JPEG_QUALITY_MAX)
        self._jpeg_quality_slider.setValue(JPEG_QUALITY_DEFAULT)
        self._jpeg_quality_slider.setTickPosition(QSlider.TickPosition.NoTicks)
        quality_row.addWidget(self._jpeg_quality_slider, stretch=1)
        self._jpeg_quality_label = QLabel(str(JPEG_QUALITY_DEFAULT))
        self._jpeg_quality_label.setFixedWidth(24)
        quality_row.addWidget(self._jpeg_quality_label)
        self._jpeg_quality_slider.valueChanged.connect(
            lambda v: self._jpeg_quality_label.setText(str(v))
        )
        export_layout.addLayout(quality_row)
        self._jpeg_quality_row_widgets = [
            quality_row.itemAt(i).widget()
            for i in range(quality_row.count()) if quality_row.itemAt(i).widget()
        ]

        # JPEG subsampling dropdown
        sub_row = QHBoxLayout()
        sub_row.addWidget(QLabel("Subsampling:"))
        self._jpeg_subsampling = QComboBox()
        self._jpeg_subsampling.addItems(JPEG_SUBSAMPLING_OPTIONS)
        self._jpeg_subsampling.setCurrentText(JPEG_SUBSAMPLING_DEFAULT)
        sub_row.addWidget(self._jpeg_subsampling)
        export_layout.addLayout(sub_row)
        self._jpeg_sub_row_widgets = [
            sub_row.itemAt(i).widget()
            for i in range(sub_row.count()) if sub_row.itemAt(i).widget()
        ]

        # Initial visibility — hide JPEG controls when PNG is selected
        self._on_export_format_changed(self._export_format.currentText())

        return export_group

    def _on_export_format_changed(self, fmt: str):
        """Show/hide JPEG-specific controls based on selected format."""
        is_jpeg = fmt == "JPEG"
        for w in self._jpeg_quality_row_widgets:
            w.setVisible(is_jpeg)
        for w in self._jpeg_sub_row_widgets:
            w.setVisible(is_jpeg)

    def _get_export_settings(self) -> dict:
        """Build export settings dict from current UI state."""
        fmt = self._export_format.currentText()  # "PNG" or "JPEG"
        return {
            "format": fmt,
            "compress_level": PNG_COMPRESS_LEVEL,
            "jpeg_quality": self._jpeg_quality_slider.value(),
            "jpeg_subsampling": JPEG_SUBSAMPLING_MAP[self._jpeg_subsampling.currentText()],
            "jpeg_optimize": True,
        }

    def _build_logo_group(self) -> QGroupBox:
        logo_group = QGroupBox("Logo Overlay")
        logo_layout = QVBoxLayout(logo_group)

        self._logo_enabled = QCheckBox("Enable Logo")
        self._logo_enabled.setChecked(False)
        self._logo_enabled.toggled.connect(self._on_logo_setting_changed)
        logo_layout.addWidget(self._logo_enabled)

        logo_file_row = QHBoxLayout()
        btn_select_logo = QPushButton("Select…")
        btn_select_logo.setFixedWidth(70)
        btn_select_logo.clicked.connect(self._select_logo)
        logo_file_row.addWidget(btn_select_logo)
        self._logo_file_label = QLabel("No logo")
        self._logo_file_label.setStyleSheet("color: #888; font-size: 8pt;")
        self._logo_file_label.setWordWrap(True)
        logo_file_row.addWidget(self._logo_file_label, stretch=1)
        logo_layout.addLayout(logo_file_row)

        # Position
        pos_row = QHBoxLayout()
        pos_row.addWidget(QLabel("Position:"))
        self._logo_position = QComboBox()
        self._logo_position.addItems(LOGO_POSITIONS)
        self._logo_position.setCurrentText("TopRight")
        self._logo_position.currentTextChanged.connect(self._on_logo_setting_changed)
        pos_row.addWidget(self._logo_position)
        logo_layout.addLayout(pos_row)

        # Size percent
        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("Size:"))
        self._logo_size = QSpinBox()
        self._logo_size.setRange(1, 100)
        self._logo_size.setValue(25)
        self._logo_size.setSuffix("%")
        self._logo_size.valueChanged.connect(self._on_logo_setting_changed)
        size_row.addWidget(self._logo_size)
        logo_layout.addLayout(size_row)

        # Base dimension
        base_row = QHBoxLayout()
        base_row.addWidget(QLabel("Of:"))
        self._logo_base_dim = QComboBox()
        self._logo_base_dim.addItems(LOGO_BASE_DIMENSIONS)
        self._logo_base_dim.setCurrentText("Shorter side")
        self._logo_base_dim.currentTextChanged.connect(self._on_logo_setting_changed)
        base_row.addWidget(self._logo_base_dim)
        logo_layout.addLayout(base_row)

        # Margin
        margin_row = QHBoxLayout()
        margin_row.addWidget(QLabel("Margin:"))
        self._logo_margin_auto = QCheckBox("Auto")
        self._logo_margin_auto.setChecked(True)
        self._logo_margin_auto.setToolTip("Compute margin from logo height × ratio")
        self._logo_margin_auto.toggled.connect(self._on_logo_margin_mode_changed)
        margin_row.addWidget(self._logo_margin_auto)
        logo_layout.addLayout(margin_row)

        # Auto margin ratio
        auto_margin_row = QHBoxLayout()
        auto_margin_row.addWidget(QLabel("  × logo height:"))
        self._logo_margin_ratio = QSpinBox()
        self._logo_margin_ratio.setRange(5, 200)
        self._logo_margin_ratio.setValue(75)
        self._logo_margin_ratio.setSuffix("%")
        self._logo_margin_ratio.setToolTip("Margin as percentage of logo height")
        self._logo_margin_ratio.valueChanged.connect(self._on_logo_setting_changed)
        auto_margin_row.addWidget(self._logo_margin_ratio)
        logo_layout.addLayout(auto_margin_row)
        self._auto_margin_row_widgets = [auto_margin_row.itemAt(i).widget() for i in range(auto_margin_row.count()) if auto_margin_row.itemAt(i).widget()]

        # Fixed margin
        fixed_margin_row = QHBoxLayout()
        fixed_margin_row.addWidget(QLabel("  Pixels:"))
        self._logo_margin_px = QSpinBox()
        self._logo_margin_px.setRange(0, 500)
        self._logo_margin_px.setValue(40)
        self._logo_margin_px.setSuffix(" px")
        self._logo_margin_px.valueChanged.connect(self._on_logo_setting_changed)
        fixed_margin_row.addWidget(self._logo_margin_px)
        logo_layout.addLayout(fixed_margin_row)
        self._fixed_margin_row_widgets = [fixed_margin_row.itemAt(i).widget() for i in range(fixed_margin_row.count()) if fixed_margin_row.itemAt(i).widget()]

        # Show/hide based on initial state
        self._on_logo_margin_mode_changed(self._logo_margin_auto.isChecked())

        return logo_group

    def _build_shortcuts_group(self) -> QGroupBox:
        help_group = QGroupBox("Shortcuts")
        help_layout = QVBoxLayout(help_group)
        help_label = QLabel(
            "Arrow keys: nudge crop (1px)\n"
            "Shift+Arrow: nudge (10px)\n"
            "Drag corners: resize crop\n"
            "Drag body: move crop\n"
            "\n"
            "Page Down / D: next image\n"
            "Page Up / A: prev image\n"
            "Tab / W: next ratio\n"
            "Shift+Tab / Q: prev ratio\n"
            "C: auto center max\n"
            "Shift+C: auto center all ratios"
        )
        help_label.setStyleSheet("color: #888; font-size: 8pt;")
        help_layout.addWidget(help_label)
        return help_group

    # =========================================================================
    # Folder selection
    # =========================================================================

    def _select_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Input Folder", str(self._input_folder or Path.home()))
        if not folder:
            return
        self._input_folder = Path(folder)
        self._load_images()

    def _select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Output Folder", str(self._output_root or Path.home()))
        if not folder:
            return
        self._output_root = Path(folder)
        self._status.showMessage(f"Output folder: {self._output_root}")

    # =========================================================================
    # Logo selection
    # =========================================================================

    def _select_logo(self):
        """Open file dialog to select a logo file (SVG or image)."""
        filter_parts = [
            "SVG files (*.svg)",
            "PNG files (*.png)",
            "All images (*.png *.jpg *.jpeg *.bmp *.webp *.svg)",
        ]
        filter_str = ";;".join(filter_parts)

        path, _ = QFileDialog.getOpenFileName(
            self, "Select Logo File",
            str(self._logo_path.parent if self._logo_path else Path.home()),
            filter_str,
        )
        if not path:
            return

        logo_path = Path(path)

        # Validate SVG support
        if logo_path.suffix.lower() == ".svg" and not HAS_MAGICK:
            QMessageBox.warning(
                self, "SVG Not Supported",
                "SVG logos require ImageMagick.\n\n"
                "Install from: https://imagemagick.org/\n\n"
                "Alternatively, use a PNG logo.",
            )
            return

        # Load logo as QPixmap for preview
        try:
            if logo_path.suffix.lower() == ".svg":
                # Rasterize SVG via ImageMagick for preview
                result = subprocess.run(
                    ["magick", "-density", "150", "-background", "none", str(logo_path), "PNG:-"],
                    capture_output=True,
                )
                if result.returncode != 0:
                    raise ValueError(f"ImageMagick error: {result.stderr.decode(errors='replace')}")
                qimg = QImage()
                qimg.loadFromData(result.stdout)
                pixmap = QPixmap.fromImage(qimg)
            else:
                pixmap = QPixmap(str(logo_path))

            if pixmap.isNull():
                raise ValueError("Could not load image")
        except Exception as e:
            QMessageBox.warning(self, "Logo Error", f"Failed to load logo:\n{e}")
            return

        self._logo_path = logo_path
        self._logo_pixmap = pixmap
        self._logo_file_label.setText(logo_path.name)
        self._logo_enabled.setChecked(True)
        self._update_logo_preview()

    def _on_logo_setting_changed(self, *args):
        """Called when any logo setting changes."""
        self._update_logo_preview()

    def _on_logo_margin_mode_changed(self, auto: bool):
        """Show/hide margin widgets based on auto/fixed mode."""
        for w in self._auto_margin_row_widgets:
            w.setVisible(auto)
        for w in self._fixed_margin_row_widgets:
            w.setVisible(not auto)
        self._on_logo_setting_changed()

    def _get_logo_config(self) -> dict | None:
        """Build logo config dict from current UI settings, or None if disabled."""
        if not self._logo_enabled.isChecked() or not self._logo_path or not self._logo_pixmap:
            return None
        if self._current_index < 0 or not self._ratios:
            return None
        r = self._ratios[self._current_ratio_idx]
        first_target = r["targets"][0]
        return {
            "position": self._logo_position.currentText(),
            "size_percent": self._logo_size.value(),
            "base_dimension": self._logo_base_dim.currentText(),
            "margin_auto": self._logo_margin_auto.isChecked(),
            "margin_ratio": self._logo_margin_ratio.value() / 100.0,
            "margin_px": self._logo_margin_px.value(),
            "target_w": first_target["target_w"],
            "target_h": first_target["target_h"],
        }

    def _get_logo_worker_settings(self) -> dict | None:
        """Build serializable logo settings for the worker process."""
        if not self._logo_enabled.isChecked() or not self._logo_path:
            return None
        return {
            "enabled": True,
            "path": str(self._logo_path),
            "position": self._logo_position.currentText(),
            "size_percent": self._logo_size.value(),
            "base_dimension": self._logo_base_dim.currentText(),
            "margin_auto": self._logo_margin_auto.isChecked(),
            "margin_ratio": self._logo_margin_ratio.value() / 100.0,
            "margin_px": self._logo_margin_px.value(),
        }

    def _update_logo_preview(self):
        """Update the crop widget with current logo overlay settings."""
        config = self._get_logo_config()
        self._crop_widget.set_logo(self._logo_pixmap if config else None, config)

    # =========================================================================
    # Image loading
    # =========================================================================

    def _load_images(self):
        self._image_states.clear()
        self._image_list.clear()
        self._current_index = -1
        self._crop_widget.clear()

        if not self._input_folder:
            return

        # Scan recursively or flat based on toggle
        self._status.showMessage("Scanning for images…")
        QApplication.processEvents()

        if self._scan_subfolders.isChecked():
            files = sorted(
                [f for f in self._input_folder.rglob("*") if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS],
                key=lambda f: str(f.relative_to(self._input_folder)).lower(),
            )
        else:
            files = sorted(
                [f for f in self._input_folder.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS],
                key=lambda f: f.name.lower(),
            )

        if not files:
            self._status.showMessage("No supported images found in the selected folder.")
            self._update_button_states()
            self._update_counter()
            return

        progress = QProgressDialog("Loading images…", "Cancel", 0, len(files), self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)

        for i, f in enumerate(files):
            if progress.wasCanceled():
                break
            progress.setValue(i)
            progress.setLabelText(f"Reading: {f.name}  ({i + 1}/{len(files)})")

            try:
                w, h = get_image_size(f)
            except Exception:
                continue

            # Compute content fingerprint for cache lookup
            try:
                fp = compute_fingerprint(f)
            except OSError:
                fp = ""

            rel = f.relative_to(self._input_folder)
            state = ImageState(path=f, rel_path=rel, img_w=w, img_h=h, fingerprint=fp)

            # Restore cached crops if available, otherwise auto-center-max
            cached = lookup_crops(self._crop_cache, fp, w, h) if fp else None
            for r in self._ratios:
                akey = aspect_key(r["ratio_w"], r["ratio_h"])
                if cached and akey in cached:
                    state.crops[akey] = cached[akey]
                else:
                    state.crops[akey] = auto_center_max(w, h, r["ratio_w"], r["ratio_h"])
            self._image_states.append(state)

            # Show relative path in list if scanning subfolders
            display_name = str(rel) if self._scan_subfolders.isChecked() else f.name
            item = QListWidgetItem(f"  ⬜  {display_name}  ({w}×{h})")
            self._image_list.addItem(item)

        progress.setValue(len(files))

        if self._image_states:
            self._image_list.setCurrentRow(0)
            self._status.showMessage(f"Loaded {len(self._image_states)} images from {self._input_folder}")
        else:
            self._status.showMessage("No supported images found in the selected folder.")

        self._save_cache()

        self._update_button_states()
        self._update_counter()

    # =========================================================================
    # Image selection
    # =========================================================================

    def _on_image_selected(self, row: int):
        if row < 0 or row >= len(self._image_states):
            self._crop_widget.clear()
            self._current_index = -1
            return

        # Save current crop before switching
        self._save_current_crop()
        self._save_cache()

        self._current_index = row
        state = self._image_states[row]

        # Mark as reviewed and update list icon
        if not state.reviewed:
            state.reviewed = True
            self._update_list_item(row)
        self._update_counter()

        # Show loading state and load image in background thread
        self._crop_widget.set_loading(True)
        self._crop_widget.clear()

        # Cancel any previous loader
        if self._loader is not None:
            try:
                self._loader.finished.disconnect()
                self._loader.error.disconnect()
            except (TypeError, RuntimeError):
                pass  # Already disconnected or destroyed
            if self._loader.isRunning():
                self._loader.quit()
                self._loader.wait(500)

        self._loader = ImageLoaderThread(state.path, self)
        self._loader.finished.connect(lambda pixmap, r=row: self._on_image_loaded(r, pixmap))
        self._loader.error.connect(lambda err: self._on_image_load_error(err))
        self._loader.start()

        self._update_button_states()

    def _on_image_loaded(self, row: int, pixmap: QPixmap):
        """Called when background image loading completes."""
        if row != self._current_index:
            return  # User navigated away before loading finished
        state = self._image_states[row]
        self._crop_widget.set_image(pixmap, state.img_w, state.img_h)
        self._apply_ratio(self._current_ratio_idx)

    def _on_image_load_error(self, error: str):
        """Called when background image loading fails."""
        self._crop_widget.set_loading(False)
        self._status.showMessage(f"Failed to load image: {error}")

    # =========================================================================
    # Ratio selection
    # =========================================================================

    def _on_ratio_selected(self, idx: int):
        self._save_current_crop()
        for i, btn in enumerate(self._ratio_buttons):
            btn.setChecked(i == idx)
        self._current_ratio_idx = idx
        self._apply_ratio(idx)

    def _apply_ratio(self, ratio_idx: int):
        if self._current_index < 0 or not self._ratios:
            return
        state = self._image_states[self._current_index]
        r = self._ratios[ratio_idx]
        akey = aspect_key(r["ratio_w"], r["ratio_h"])
        crop = state.crops.get(akey)
        if not crop:
            crop = auto_center_max(state.img_w, state.img_h, r["ratio_w"], r["ratio_h"])
            state.crops[akey] = crop
        self._crop_widget.set_crop(crop, r["ratio_w"] / r["ratio_h"])
        self._update_crop_info()
        self._update_logo_preview()

    def _save_current_crop(self):
        if self._current_index < 0 or not self._ratios:
            return
        # Don't save while the image is still loading asynchronously —
        # the widget crop would be stale (previous image) or default (0×0).
        if not self._crop_widget.has_image():
            return
        state = self._image_states[self._current_index]
        r = self._ratios[self._current_ratio_idx]
        akey = aspect_key(r["ratio_w"], r["ratio_h"])
        state.crops[akey] = self._crop_widget.get_crop()
        # Update in-memory cache
        if state.fingerprint:
            store_crops(self._crop_cache, state.fingerprint,
                        state.img_w, state.img_h, state.crops)

    def _on_crop_changed(self):
        self._save_current_crop()
        self._update_crop_info()
        self._update_logo_preview()

    def _update_crop_info(self):
        if self._current_index < 0 or not self._ratios:
            self._crop_info_label.setText("Crop: —")
            return
        crop = self._crop_widget.get_crop()
        r = self._ratios[self._current_ratio_idx]
        targets = r.get("targets", [])
        exports = ", ".join(f"{t['target_w']}×{t['target_h']}" for t in targets)
        self._crop_info_label.setText(
            f"Crop: {crop.w}×{crop.h}\n"
            f"Position: ({crop.x}, {crop.y})\n"
            f"Exports to: {exports}"
        )

    # =========================================================================
    # Auto center
    # =========================================================================

    def _auto_center_current(self):
        if self._current_index < 0 or not self._ratios:
            return
        state = self._image_states[self._current_index]
        r = self._ratios[self._current_ratio_idx]
        akey = aspect_key(r["ratio_w"], r["ratio_h"])
        crop = auto_center_max(state.img_w, state.img_h, r["ratio_w"], r["ratio_h"])
        state.crops[akey] = crop
        self._crop_widget.set_crop(crop, r["ratio_w"] / r["ratio_h"])
        self._update_crop_info()
        # Update cache with reset crop
        if state.fingerprint:
            store_crops(self._crop_cache, state.fingerprint,
                        state.img_w, state.img_h, state.crops)

    def _auto_center_all_ratios(self):
        if self._current_index < 0:
            return
        state = self._image_states[self._current_index]
        for r in self._ratios:
            akey = aspect_key(r["ratio_w"], r["ratio_h"])
            state.crops[akey] = auto_center_max(state.img_w, state.img_h, r["ratio_w"], r["ratio_h"])
        self._apply_ratio(self._current_ratio_idx)
        # Update cache with all reset crops
        if state.fingerprint:
            store_crops(self._crop_cache, state.fingerprint,
                        state.img_w, state.img_h, state.crops)

    # =========================================================================
    # Navigation
    # =========================================================================

    def _prev_image(self):
        if self._current_index > 0:
            self._on_ratio_selected(0)  # Reset to first ratio
            self._image_list.setCurrentRow(self._current_index - 1)

    def _next_image(self):
        if self._current_index < len(self._image_states) - 1:
            self._on_ratio_selected(0)  # Reset to first ratio
            self._image_list.setCurrentRow(self._current_index + 1)

    def _next_ratio(self):
        if len(self._ratios) == 0:
            return
        idx = (self._current_ratio_idx + 1) % len(self._ratios)
        self._on_ratio_selected(idx)

    def _prev_ratio(self):
        if len(self._ratios) == 0:
            return
        idx = (self._current_ratio_idx - 1) % len(self._ratios)
        self._on_ratio_selected(idx)

    def _update_button_states(self):
        has_images = len(self._image_states) > 0
        has_ratios = len(self._ratios) > 0
        self._btn_prev.setEnabled(self._current_index > 0)
        self._btn_next.setEnabled(self._current_index < len(self._image_states) - 1)
        self._act_process_current.setEnabled(has_images and has_ratios and self._current_index >= 0)
        self._act_process_all_manual.setEnabled(has_images and has_ratios)

    # =========================================================================
    # Processing / export
    # =========================================================================

    def _ensure_output_folder(self) -> bool:
        if not self._output_root:
            self._select_output_folder()
        if not self._output_root:
            QMessageBox.warning(self, "No Output Folder", "Please select an output folder first.")
            return False
        return True

    def _process_image(self, state: ImageState):
        """Process a single image: crop, resize, and save for all ratio groups and targets."""
        img = open_image(state.path)
        img = img.convert("RGB")  # Flatten (removes alpha, layers)

        logo_settings = self._get_logo_worker_settings()

        for group in self._ratios:
            akey = aspect_key(group["ratio_w"], group["ratio_h"])
            crop = state.crops.get(akey)
            if not crop:
                crop = auto_center_max(state.img_w, state.img_h, group["ratio_w"], group["ratio_h"])

            # Crop once per aspect ratio group
            cropped = img.crop((crop.x, crop.y, crop.x + crop.w, crop.y + crop.h))

            for target in group["targets"]:
                # Resize to each target resolution
                target_size = (target["target_w"], target["target_h"])
                resized = cropped.resize(target_size, Image.Resampling.LANCZOS)

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

                # Save — recreate subfolder structure from input
                out_dir = self._output_root / target["folder"]
                if state.rel_path and state.rel_path.parent != Path("."):
                    out_dir = out_dir / state.rel_path.parent
                out_dir.mkdir(parents=True, exist_ok=True)

                export = self._get_export_settings()
                if export["format"] == "JPEG":
                    out_path = unique_path(out_dir / f"{state.path.stem}.jpg")
                    resized.save(
                        str(out_path), "JPEG",
                        quality=export["jpeg_quality"],
                        optimize=export["jpeg_optimize"],
                        subsampling=export["jpeg_subsampling"],
                    )
                else:
                    out_path = unique_path(out_dir / f"{state.path.stem}.png")
                    resized.save(str(out_path), "PNG", compress_level=export["compress_level"])

        state.processed = True

    def _process_current(self):
        if not self._ensure_output_folder():
            return
        if self._current_index < 0:
            return
        self._save_current_crop()
        state = self._image_states[self._current_index]

        progress = QProgressDialog(f"Exporting: {state.path.name}…", None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QApplication.processEvents()

        args = self._build_worker_args(self._current_index, state)

        with ProcessPoolExecutor(max_workers=1) as executor:
            future = executor.submit(process_worker, args)
            while not future.done():
                QApplication.processEvents()
                time.sleep(0.05)

            result = future.result()

        progress.close()

        if result["success"]:
            self._image_states[result["index"]].processed = True
            self._mark_processed(result["index"])
            self._status.showMessage(f"Exported: {state.path.name}")
        else:
            QMessageBox.critical(self, "Error", f"Failed to process {state.path.name}:\n{result['error']}")

    def _build_worker_args(self, index: int, state: ImageState) -> dict:
        """Build serializable arguments for the parallel worker."""
        crops_serial = {}
        for akey, crop in state.crops.items():
            crops_serial[akey] = (crop.x, crop.y, crop.w, crop.h)
        rel_parent = str(state.rel_path.parent) if state.rel_path and state.rel_path.parent != Path(".") else None
        return {
            "index": index,
            "path": str(state.path),
            "img_w": state.img_w,
            "img_h": state.img_h,
            "crops": crops_serial,
            "ratios": self._ratios,
            "output_root": str(self._output_root),
            "rel_parent": rel_parent,
            "export": self._get_export_settings(),
            "logo": self._get_logo_worker_settings(),
        }

    def _run_batch(self):
        """Run batch export using current crops. Uses parallel processing."""
        if not self._ensure_output_folder():
            return

        self._save_current_crop()

        total = len(self._image_states)
        progress = QProgressDialog("Preparing export…", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        QApplication.processEvents()

        workers = max(1, (os.cpu_count() or 4) - 1)  # Leave one core free for UI
        args_list = [self._build_worker_args(i, s) for i, s in enumerate(self._image_states)]
        completed = 0
        errors = []

        progress.setLabelText("Starting workers…")
        QApplication.processEvents()

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(process_worker, args): args["index"] for args in args_list}

            for future in as_completed(futures):
                if progress.wasCanceled():
                    executor.shutdown(wait=False, cancel_futures=True)
                    break

                result = future.result()
                completed += 1
                progress.setValue(completed)
                progress.setLabelText(f"Exporting: {result['name']}  ({completed}/{total})")
                QApplication.processEvents()

                if result["success"]:
                    self._image_states[result["index"]].processed = True
                    self._mark_processed(result["index"])
                else:
                    errors.append(result)

        progress.setValue(total)

        if errors:
            err_names = "\n".join(f"• {e['name']}: {e['error']}" for e in errors[:10])
            suffix = f"\n…and {len(errors) - 10} more" if len(errors) > 10 else ""
            QMessageBox.warning(self, "Some exports failed", f"{len(errors)} failed:\n\n{err_names}{suffix}")

        self._status.showMessage(f"Export complete ({completed - len(errors)}/{total}). Output: {self._output_root}")

        if self._current_index >= 0:
            self._apply_ratio(self._current_ratio_idx)

    def _process_all_manual(self):
        """Export all images using their current crops."""
        self._run_batch()

    # =========================================================================
    # List item updates
    # =========================================================================

    def _update_list_item(self, index: int):
        """Update the list item icon/text based on state."""
        item = self._image_list.item(index)
        if not item:
            return
        state = self._image_states[index]
        display_name = str(state.rel_path) if self._scan_subfolders.isChecked() else state.path.name

        if state.processed:
            icon = "✅"
        elif state.reviewed:
            icon = "👁"
        else:
            icon = "⬜"

        item.setText(f"  {icon}  {display_name}  ({state.img_w}×{state.img_h})")

    def _mark_processed(self, index: int):
        self._image_states[index].processed = True
        self._update_list_item(index)
        self._update_counter()

    def _update_counter(self):
        """Update the progress counter in the status bar or counter label."""
        total = len(self._image_states)
        if total == 0:
            self._counter_label.setText("")
            return
        reviewed = sum(1 for s in self._image_states if s.reviewed)
        exported = sum(1 for s in self._image_states if s.processed)
        self._counter_label.setText(
            f"  👁 {reviewed}/{total} reviewed  ·  ✅ {exported}/{total} exported  "
        )

    # =========================================================================
    # Cache persistence
    # =========================================================================

    def _save_cache(self):
        """Flush the in-memory crop cache to disk."""
        save_crop_cache(self._crop_cache)

    def closeEvent(self, event):
        """Save current crop and flush cache before closing."""
        self._save_current_crop()
        self._save_cache()
        super().closeEvent(event)
