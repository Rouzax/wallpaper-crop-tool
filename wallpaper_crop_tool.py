#!/usr/bin/env python3
"""
Wallpaper Batch Crop Tool
=========================
Interactive tool for cropping and resizing wallpapers to multiple screen ratios.

Requirements: pip install PyQt6 Pillow

Usage: python wallpaper_crop_tool.py
"""

import sys
import os
import time
from pathlib import Path
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor, as_completed
from PIL import Image
from psd_tools import PSDImage

# Allow very large images (Pillow's default limit is ~178MP)
Image.MAX_IMAGE_PIXELS = None

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPushButton, QLabel, QFileDialog,
    QSplitter, QGroupBox, QMessageBox, QProgressDialog, QStatusBar,
    QToolBar, QSizePolicy, QCheckBox
)
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, QSize, pyqtSignal, QThread
from PyQt6.QtGui import (
    QPainter, QPixmap, QColor, QPen, QBrush, QImage,
    QKeyEvent, QMouseEvent, QPaintEvent, QResizeEvent, QAction, QIcon,
    QKeySequence, QShortcut
)

# =============================================================================
# RATIO CONFIGURATION — Edit this list to add/remove screen ratios
# =============================================================================
RATIOS = [
    {
        "name": "16:9",
        "ratio_w": 16,
        "ratio_h": 9,
        "target_w": 3840,
        "target_h": 2160,
        "folder": "Ratio 16x9",
    },
    {
        "name": "16:10",
        "ratio_w": 16,
        "ratio_h": 10,
        "target_w": 3840,
        "target_h": 2400,
        "folder": "Ratio 16x10",
    },
    {
        "name": "12:5",
        "ratio_w": 12,
        "ratio_h": 5,
        "target_w": 3840,
        "target_h": 1600,
        "folder": "Ratio 12x5",
    },
]

# PNG compression level (0-9, 9 = maximum compression)
PNG_COMPRESS_LEVEL = 9

# Supported image extensions
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp", ".psd"}

# Nudge amounts (pixels in image coordinates)
NUDGE_SMALL = 1
NUDGE_LARGE = 10

# Minimum crop size (pixels)
MIN_CROP_SIZE = 50

# Handle size for resize corners (pixels in screen coordinates)
HANDLE_SIZE = 10


def pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    """Convert a PIL Image to QPixmap."""
    img_rgb = pil_img.convert("RGBA")
    data = img_rgb.tobytes("raw", "RGBA")
    qimg = QImage(data, img_rgb.width, img_rgb.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def load_pixmap(path: Path) -> QPixmap:
    """Load a QPixmap from any supported image file."""
    if path.suffix.lower() == ".psd":
        pil_img = open_image(path)
        return pil_to_qpixmap(pil_img)
    return QPixmap(str(path))


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


# =============================================================================
# Data classes
# =============================================================================

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


def _process_worker(args: dict) -> dict:
    """Worker function for parallel image processing. Runs in a separate process."""
    # Re-apply settings in the worker process
    Image.MAX_IMAGE_PIXELS = None

    idx = args["index"]
    img_path = Path(args["path"])
    img_w = args["img_w"]
    img_h = args["img_h"]
    crops = args["crops"]  # {ratio_name: (x, y, w, h)}
    ratios = args["ratios"]
    output_root = Path(args["output_root"])
    rel_parent = args["rel_parent"]  # str or None
    compress = args["compress_level"]

    try:
        if img_path.suffix.lower() == ".psd":
            psd = PSDImage.open(str(img_path))
            img = psd.composite()
        else:
            img = Image.open(img_path)
        img = img.convert("RGB")

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

            out_dir = output_root / r["folder"]
            if rel_parent:
                out_dir = out_dir / rel_parent
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = unique_path(out_dir / f"{img_path.stem}.png")
            resized.save(str(out_path), "PNG", compress_level=compress)

        return {"index": idx, "success": True, "name": img_path.name}
    except Exception as e:
        return {"index": idx, "success": False, "name": img_path.name, "error": str(e)}


class ImageLoaderThread(QThread):
    """Background thread for loading/compositing images (especially large PSDs)."""
    finished = pyqtSignal(QPixmap)
    error = pyqtSignal(str)

    def __init__(self, path: Path, parent=None):
        super().__init__(parent)
        self._path = path

    def run(self):
        try:
            pixmap = load_pixmap(self._path)
            self.finished.emit(pixmap)
        except Exception as e:
            self.error.emit(str(e))
@dataclass
class CropRect:
    """Crop rectangle in image coordinates."""
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


@dataclass
class ImageState:
    """Tracks crop state for one image across all ratios."""
    path: Path = None
    rel_path: Path = None  # relative path from input root (including filename)
    img_w: int = 0
    img_h: int = 0
    crops: dict = field(default_factory=dict)  # ratio_name -> CropRect
    reviewed: bool = False   # user has visited this image
    processed: bool = False  # image has been exported


# =============================================================================
# Crop math utilities
# =============================================================================
def calculate_max_crop(img_w: int, img_h: int, ratio_w: int, ratio_h: int) -> tuple[int, int]:
    """Calculate the maximum crop dimensions for a given aspect ratio within an image."""
    aspect = ratio_w / ratio_h
    # Try full width
    crop_w = img_w
    crop_h = int(round(crop_w / aspect))
    if crop_h <= img_h:
        return crop_w, crop_h
    # Full height
    crop_h = img_h
    crop_w = int(round(crop_h * aspect))
    return min(crop_w, img_w), crop_h


def center_crop(img_w: int, img_h: int, crop_w: int, crop_h: int) -> CropRect:
    """Return a centered crop rectangle."""
    x = (img_w - crop_w) // 2
    y = (img_h - crop_h) // 2
    return CropRect(x, y, crop_w, crop_h)


def auto_center_max(img_w: int, img_h: int, ratio_w: int, ratio_h: int) -> CropRect:
    """Maximum crop, centered."""
    cw, ch = calculate_max_crop(img_w, img_h, ratio_w, ratio_h)
    return center_crop(img_w, img_h, cw, ch)


def clamp_crop(crop: CropRect, img_w: int, img_h: int) -> CropRect:
    """Clamp crop rectangle to image bounds."""
    w = max(MIN_CROP_SIZE, min(crop.w, img_w))
    h = max(MIN_CROP_SIZE, min(crop.h, img_h))
    x = max(0, min(crop.x, img_w - w))
    y = max(0, min(crop.y, img_h - h))
    return CropRect(x, y, w, h)


# =============================================================================
# Image Crop Widget — interactive crop overlay on image
# =============================================================================
class ImageCropWidget(QWidget):
    """Widget that displays an image with an interactive, resizable crop overlay."""

    crop_changed = pyqtSignal()

    HANDLE_NONE = 0
    HANDLE_TL = 1
    HANDLE_TR = 2
    HANDLE_BL = 3
    HANDLE_BR = 4
    MODE_NONE = 0
    MODE_MOVE = 1
    MODE_RESIZE = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._pixmap: QPixmap | None = None
        self._img_w = 0
        self._img_h = 0
        self._crop = CropRect()
        self._aspect_ratio = 16 / 9  # locked aspect ratio

        # Display mapping
        self._scale = 1.0
        self._offset_x = 0.0
        self._offset_y = 0.0

        # Interaction state
        self._mode = self.MODE_NONE
        self._active_handle = self.HANDLE_NONE
        self._drag_start = QPointF()
        self._crop_start = CropRect()
        self._loading = False

    def set_loading(self, loading: bool):
        """Show/hide loading indicator."""
        self._loading = loading
        self.update()

    def set_image(self, pixmap: QPixmap, img_w: int, img_h: int):
        """Set the image to display."""
        self._loading = False
        self._pixmap = pixmap
        self._img_w = img_w
        self._img_h = img_h
        self._update_display_mapping()
        self.update()

    def set_crop(self, crop: CropRect, aspect_ratio: float):
        """Set the crop rectangle and locked aspect ratio."""
        self._aspect_ratio = aspect_ratio
        self._crop = CropRect(crop.x, crop.y, crop.w, crop.h)
        self.update()

    def get_crop(self) -> CropRect:
        return CropRect(self._crop.x, self._crop.y, self._crop.w, self._crop.h)

    def clear(self):
        self._pixmap = None
        self._img_w = 0
        self._img_h = 0
        self.update()

    # --- Coordinate mapping ---

    def _update_display_mapping(self):
        """Calculate scale and offset to fit image in widget with letterboxing."""
        if not self._pixmap or self._img_w == 0 or self._img_h == 0:
            return
        ww, wh = self.width(), self.height()
        scale_x = ww / self._img_w
        scale_y = wh / self._img_h
        self._scale = min(scale_x, scale_y)
        disp_w = self._img_w * self._scale
        disp_h = self._img_h * self._scale
        self._offset_x = (ww - disp_w) / 2
        self._offset_y = (wh - disp_h) / 2

    def _img_to_display(self, ix: float, iy: float) -> QPointF:
        return QPointF(ix * self._scale + self._offset_x, iy * self._scale + self._offset_y)

    def _display_to_img(self, dx: float, dy: float) -> QPointF:
        if self._scale == 0:
            return QPointF(0, 0)
        return QPointF((dx - self._offset_x) / self._scale, (dy - self._offset_y) / self._scale)

    def _crop_display_rect(self) -> QRectF:
        tl = self._img_to_display(self._crop.x, self._crop.y)
        br = self._img_to_display(self._crop.x + self._crop.w, self._crop.y + self._crop.h)
        return QRectF(tl, br)

    # --- Handle hit testing ---

    def _handle_rects(self) -> dict[int, QRectF]:
        """Return screen-coordinate rectangles for the 4 corner handles."""
        r = self._crop_display_rect()
        hs = HANDLE_SIZE
        return {
            self.HANDLE_TL: QRectF(r.left() - hs, r.top() - hs, hs * 2, hs * 2),
            self.HANDLE_TR: QRectF(r.right() - hs, r.top() - hs, hs * 2, hs * 2),
            self.HANDLE_BL: QRectF(r.left() - hs, r.bottom() - hs, hs * 2, hs * 2),
            self.HANDLE_BR: QRectF(r.right() - hs, r.bottom() - hs, hs * 2, hs * 2),
        }

    def _hit_test(self, pos: QPointF) -> tuple[int, int]:
        """Returns (mode, handle) for a screen position."""
        handles = self._handle_rects()
        for handle_id, rect in handles.items():
            if rect.contains(pos):
                return self.MODE_RESIZE, handle_id
        if self._crop_display_rect().contains(pos):
            return self.MODE_MOVE, self.HANDLE_NONE
        return self.MODE_NONE, self.HANDLE_NONE

    # --- Painting ---

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), QColor(30, 30, 30))

        if not self._pixmap:
            painter.setPen(QColor(128, 128, 128))
            msg = "Loading image…" if self._loading else "No image loaded"
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, msg)
            painter.end()
            return

        # Draw image
        tl = self._img_to_display(0, 0)
        br = self._img_to_display(self._img_w, self._img_h)
        dest = QRectF(tl, br)
        painter.drawPixmap(dest.toRect(), self._pixmap)

        # Dim area outside crop
        crop_rect = self._crop_display_rect()
        dim = QColor(0, 0, 0, 140)

        # Top strip
        painter.fillRect(QRectF(dest.left(), dest.top(), dest.width(), crop_rect.top() - dest.top()), dim)
        # Bottom strip
        painter.fillRect(QRectF(dest.left(), crop_rect.bottom(), dest.width(), dest.bottom() - crop_rect.bottom()), dim)
        # Left strip
        painter.fillRect(QRectF(dest.left(), crop_rect.top(), crop_rect.left() - dest.left(), crop_rect.height()), dim)
        # Right strip
        painter.fillRect(QRectF(crop_rect.right(), crop_rect.top(), dest.right() - crop_rect.right(), crop_rect.height()), dim)

        # Draw crop border
        pen = QPen(QColor(255, 255, 255), 2)
        painter.setPen(pen)
        painter.drawRect(crop_rect)

        # Draw rule-of-thirds lines
        pen_thirds = QPen(QColor(255, 255, 255, 80), 1, Qt.PenStyle.DashLine)
        painter.setPen(pen_thirds)
        for i in range(1, 3):
            x = crop_rect.left() + crop_rect.width() * i / 3
            painter.drawLine(QPointF(x, crop_rect.top()), QPointF(x, crop_rect.bottom()))
            y = crop_rect.top() + crop_rect.height() * i / 3
            painter.drawLine(QPointF(crop_rect.left(), y), QPointF(crop_rect.right(), y))

        # Draw corner handles
        handle_brush = QBrush(QColor(255, 255, 255))
        handle_pen = QPen(QColor(0, 0, 0), 1)
        painter.setPen(handle_pen)
        painter.setBrush(handle_brush)
        for rect in self._handle_rects().values():
            painter.drawRect(rect)

        # Draw crop size label
        painter.setPen(QColor(255, 255, 255))
        label = f"{self._crop.w} × {self._crop.h}"
        painter.drawText(
            crop_rect.adjusted(0, -20, 0, 0).toRect(),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
            label,
        )

        painter.end()

    def resizeEvent(self, event: QResizeEvent):
        self._update_display_mapping()
        super().resizeEvent(event)

    # --- Mouse interaction ---

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton or not self._pixmap:
            return
        pos = event.position()
        self._mode, self._active_handle = self._hit_test(pos)
        if self._mode != self.MODE_NONE:
            self._drag_start = pos
            self._crop_start = CropRect(self._crop.x, self._crop.y, self._crop.w, self._crop.h)

    def mouseMoveEvent(self, event: QMouseEvent):
        if not self._pixmap:
            return

        pos = event.position()

        # Update cursor
        if self._mode == self.MODE_NONE:
            mode, handle = self._hit_test(pos)
            if mode == self.MODE_RESIZE:
                if handle in (self.HANDLE_TL, self.HANDLE_BR):
                    self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                else:
                    self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif mode == self.MODE_MOVE:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

        if self._mode == self.MODE_MOVE:
            delta_img = self._display_to_img(pos.x(), pos.y()) - self._display_to_img(
                self._drag_start.x(), self._drag_start.y()
            )
            new_x = self._crop_start.x + int(delta_img.x())
            new_y = self._crop_start.y + int(delta_img.y())
            self._crop.x = max(0, min(new_x, self._img_w - self._crop.w))
            self._crop.y = max(0, min(new_y, self._img_h - self._crop.h))
            self.crop_changed.emit()
            self.update()

        elif self._mode == self.MODE_RESIZE:
            self._resize_from_handle(pos)
            self.crop_changed.emit()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._mode = self.MODE_NONE
            self._active_handle = self.HANDLE_NONE

    def _resize_from_handle(self, mouse_pos: QPointF):
        """Resize crop from a corner handle, maintaining aspect ratio."""
        img_pos = self._display_to_img(mouse_pos.x(), mouse_pos.y())
        mx = max(0, min(img_pos.x(), self._img_w))
        my = max(0, min(img_pos.y(), self._img_h))

        cs = self._crop_start
        ar = self._aspect_ratio

        if self._active_handle == self.HANDLE_BR:
            anchor_x, anchor_y = cs.x, cs.y
            dw = mx - anchor_x
            dh = my - anchor_y
        elif self._active_handle == self.HANDLE_BL:
            anchor_x, anchor_y = cs.x + cs.w, cs.y
            dw = anchor_x - mx
            dh = my - anchor_y
        elif self._active_handle == self.HANDLE_TR:
            anchor_x, anchor_y = cs.x, cs.y + cs.h
            dw = mx - anchor_x
            dh = anchor_y - my
        elif self._active_handle == self.HANDLE_TL:
            anchor_x, anchor_y = cs.x + cs.w, cs.y + cs.h
            dw = anchor_x - mx
            dh = anchor_y - my
        else:
            return

        # Determine size from the limiting dimension, maintaining AR
        dw = max(dw, MIN_CROP_SIZE)
        dh = max(dh, MIN_CROP_SIZE)

        if dw / dh > ar:
            new_w = int(dh * ar)
            new_h = int(dh)
        else:
            new_w = int(dw)
            new_h = int(dw / ar)

        new_w = max(MIN_CROP_SIZE, new_w)
        new_h = max(MIN_CROP_SIZE, new_h)

        # Clamp to image bounds from anchor
        if self._active_handle in (self.HANDLE_BR, self.HANDLE_TR):
            max_w = self._img_w - anchor_x
        else:
            max_w = anchor_x

        if self._active_handle in (self.HANDLE_BR, self.HANDLE_BL):
            max_h = self._img_h - anchor_y
        else:
            max_h = anchor_y

        if new_w > max_w:
            new_w = max_w
            new_h = int(new_w / ar)
        if new_h > max_h:
            new_h = max_h
            new_w = int(new_h * ar)

        # Calculate new x, y
        if self._active_handle in (self.HANDLE_BR, self.HANDLE_TR):
            new_x = anchor_x
        else:
            new_x = anchor_x - new_w

        if self._active_handle in (self.HANDLE_BR, self.HANDLE_BL):
            new_y = anchor_y
        else:
            new_y = anchor_y - new_h

        self._crop = clamp_crop(CropRect(int(new_x), int(new_y), int(new_w), int(new_h)), self._img_w, self._img_h)

    # --- Keyboard nudge ---

    def keyPressEvent(self, event: QKeyEvent):
        if not self._pixmap:
            return
        amount = NUDGE_LARGE if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else NUDGE_SMALL
        moved = False
        if event.key() == Qt.Key.Key_Left:
            self._crop.x = max(0, self._crop.x - amount)
            moved = True
        elif event.key() == Qt.Key.Key_Right:
            self._crop.x = min(self._img_w - self._crop.w, self._crop.x + amount)
            moved = True
        elif event.key() == Qt.Key.Key_Up:
            self._crop.y = max(0, self._crop.y - amount)
            moved = True
        elif event.key() == Qt.Key.Key_Down:
            self._crop.y = min(self._img_h - self._crop.h, self._crop.y + amount)
            moved = True

        if moved:
            self.crop_changed.emit()
            self.update()
        else:
            super().keyPressEvent(event)


# =============================================================================
# Main Window
# =============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Wallpaper Batch Crop Tool")
        self.resize(1280, 800)

        self._image_states: list[ImageState] = []
        self._current_index = -1
        self._current_ratio_idx = 0
        self._output_root: Path | None = None
        self._input_folder: Path | None = None
        self._loader: ImageLoaderThread | None = None

        self._build_ui()
        self._update_button_states()

    def _build_ui(self):
        # --- Toolbar ---
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

        # --- Central layout ---
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # Left panel — image list
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("Images:"))
        self._image_list = QListWidget()
        self._image_list.currentRowChanged.connect(self._on_image_selected)
        left_layout.addWidget(self._image_list)

        self._counter_label = QLabel("")
        self._counter_label.setStyleSheet("color: #aaa; font-size: 11px; padding: 2px;")
        left_layout.addWidget(self._counter_label)

        splitter.addWidget(left_panel)

        # Center panel — crop editor
        center_panel = QWidget()
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(0, 0, 0, 0)

        self._crop_widget = ImageCropWidget()
        self._crop_widget.crop_changed.connect(self._on_crop_changed)
        center_layout.addWidget(self._crop_widget, stretch=1)

        splitter.addWidget(center_panel)

        # Right panel — ratio selector & actions
        right_panel = QWidget()
        right_panel.setFixedWidth(220)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 0, 0, 0)

        # Ratio buttons
        ratio_group = QGroupBox("Aspect Ratios")
        ratio_layout = QVBoxLayout(ratio_group)
        self._ratio_buttons: list[QPushButton] = []
        for i, r in enumerate(RATIOS):
            btn = QPushButton(f"{r['name']}  ({r['target_w']}×{r['target_h']})")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, idx=i: self._on_ratio_selected(idx))
            ratio_layout.addWidget(btn)
            self._ratio_buttons.append(btn)
        if self._ratio_buttons:
            self._ratio_buttons[0].setChecked(True)
        right_layout.addWidget(ratio_group)

        # Crop info
        self._crop_info_label = QLabel("Crop: —")
        self._crop_info_label.setWordWrap(True)
        right_layout.addWidget(self._crop_info_label)

        # Action buttons
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

        right_layout.addWidget(actions_group)

        # Keyboard shortcut help
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
        help_label.setStyleSheet("color: #888; font-size: 11px;")
        help_layout.addWidget(help_label)
        right_layout.addWidget(help_group)

        right_layout.addStretch()
        splitter.addWidget(right_panel)

        splitter.setSizes([200, 800, 220])

        # Status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._status.showMessage("Select an input folder to begin.")

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

    # --- Folder selection ---

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

    # --- Image loading ---

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

            rel = f.relative_to(self._input_folder)
            state = ImageState(path=f, rel_path=rel, img_w=w, img_h=h)
            # Initialize crops to auto-center-max for all ratios
            for r in RATIOS:
                state.crops[r["name"]] = auto_center_max(w, h, r["ratio_w"], r["ratio_h"])
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

        self._update_button_states()
        self._update_counter()

    # --- Image selection ---

    def _on_image_selected(self, row: int):
        if row < 0 or row >= len(self._image_states):
            self._crop_widget.clear()
            self._current_index = -1
            return

        # Save current crop before switching
        self._save_current_crop()

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
        if self._loader is not None and self._loader.isRunning():
            self._loader.disconnect()

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

    # --- Ratio selection ---

    def _on_ratio_selected(self, idx: int):
        self._save_current_crop()
        for i, btn in enumerate(self._ratio_buttons):
            btn.setChecked(i == idx)
        self._current_ratio_idx = idx
        self._apply_ratio(idx)

    def _apply_ratio(self, ratio_idx: int):
        if self._current_index < 0:
            return
        state = self._image_states[self._current_index]
        r = RATIOS[ratio_idx]
        crop = state.crops.get(r["name"])
        if not crop:
            crop = auto_center_max(state.img_w, state.img_h, r["ratio_w"], r["ratio_h"])
            state.crops[r["name"]] = crop
        self._crop_widget.set_crop(crop, r["ratio_w"] / r["ratio_h"])
        self._update_crop_info()

    def _save_current_crop(self):
        if self._current_index < 0:
            return
        state = self._image_states[self._current_index]
        r = RATIOS[self._current_ratio_idx]
        state.crops[r["name"]] = self._crop_widget.get_crop()

    def _on_crop_changed(self):
        self._save_current_crop()
        self._update_crop_info()

    def _update_crop_info(self):
        if self._current_index < 0:
            self._crop_info_label.setText("Crop: —")
            return
        crop = self._crop_widget.get_crop()
        r = RATIOS[self._current_ratio_idx]
        self._crop_info_label.setText(
            f"Crop: {crop.w}×{crop.h}\n"
            f"Position: ({crop.x}, {crop.y})\n"
            f"Target: {r['target_w']}×{r['target_h']}"
        )

    # --- Auto center ---

    def _auto_center_current(self):
        if self._current_index < 0:
            return
        state = self._image_states[self._current_index]
        r = RATIOS[self._current_ratio_idx]
        crop = auto_center_max(state.img_w, state.img_h, r["ratio_w"], r["ratio_h"])
        state.crops[r["name"]] = crop
        self._crop_widget.set_crop(crop, r["ratio_w"] / r["ratio_h"])
        self._update_crop_info()

    def _auto_center_all_ratios(self):
        if self._current_index < 0:
            return
        state = self._image_states[self._current_index]
        for r in RATIOS:
            state.crops[r["name"]] = auto_center_max(state.img_w, state.img_h, r["ratio_w"], r["ratio_h"])
        self._apply_ratio(self._current_ratio_idx)

    # --- Navigation ---

    def _prev_image(self):
        if self._current_index > 0:
            self._on_ratio_selected(0)  # Reset to first ratio
            self._image_list.setCurrentRow(self._current_index - 1)

    def _next_image(self):
        if self._current_index < len(self._image_states) - 1:
            self._on_ratio_selected(0)  # Reset to first ratio
            self._image_list.setCurrentRow(self._current_index + 1)

    def _next_ratio(self):
        if len(RATIOS) == 0:
            return
        idx = (self._current_ratio_idx + 1) % len(RATIOS)
        self._on_ratio_selected(idx)

    def _prev_ratio(self):
        if len(RATIOS) == 0:
            return
        idx = (self._current_ratio_idx - 1) % len(RATIOS)
        self._on_ratio_selected(idx)

    def _update_button_states(self):
        has_images = len(self._image_states) > 0
        self._btn_prev.setEnabled(self._current_index > 0)
        self._btn_next.setEnabled(self._current_index < len(self._image_states) - 1)
        self._act_process_current.setEnabled(has_images and self._current_index >= 0)
        self._act_process_all_manual.setEnabled(has_images)

    # --- Processing ---

    def _ensure_output_folder(self) -> bool:
        if not self._output_root:
            self._select_output_folder()
        if not self._output_root:
            QMessageBox.warning(self, "No Output Folder", "Please select an output folder first.")
            return False
        return True

    def _process_image(self, state: ImageState):
        """Process a single image: crop, resize, and save for all ratios."""
        img = open_image(state.path)
        img = img.convert("RGB")  # Flatten (removes alpha, layers)

        for r in RATIOS:
            crop = state.crops.get(r["name"])
            if not crop:
                crop = auto_center_max(state.img_w, state.img_h, r["ratio_w"], r["ratio_h"])

            # Crop
            cropped = img.crop((crop.x, crop.y, crop.x + crop.w, crop.y + crop.h))

            # Resize to target
            target_size = (r["target_w"], r["target_h"])
            resized = cropped.resize(target_size, Image.Resampling.LANCZOS)

            # Save — recreate subfolder structure from input
            out_dir = self._output_root / r["folder"]
            if state.rel_path and state.rel_path.parent != Path("."):
                out_dir = out_dir / state.rel_path.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = unique_path(out_dir / f"{state.path.stem}.png")
            resized.save(str(out_path), "PNG", compress_level=PNG_COMPRESS_LEVEL)

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
            future = executor.submit(_process_worker, args)
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
        for name, crop in state.crops.items():
            crops_serial[name] = (crop.x, crop.y, crop.w, crop.h)
        rel_parent = str(state.rel_path.parent) if state.rel_path and state.rel_path.parent != Path(".") else None
        return {
            "index": index,
            "path": str(state.path),
            "img_w": state.img_w,
            "img_h": state.img_h,
            "crops": crops_serial,
            "ratios": RATIOS,
            "output_root": str(self._output_root),
            "rel_parent": rel_parent,
            "compress_level": PNG_COMPRESS_LEVEL,
        }

    def _run_batch(self):
        """Run batch export using current crops. Uses parallel processing."""
        if not self._ensure_output_folder():
            return

        self._save_current_crop()

        total = len(self._image_states)
        progress = QProgressDialog("Exporting…", "Cancel", 0, total, self)
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)

        workers = max(1, (os.cpu_count() or 4) - 1)  # Leave one core free for UI
        args_list = [self._build_worker_args(i, s) for i, s in enumerate(self._image_states)]
        completed = 0
        errors = []

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_process_worker, args): args["index"] for args in args_list}

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


# =============================================================================
# Entry point
# =============================================================================
def main():
    app = QApplication(sys.argv)

    # Dark-ish stylesheet for a clean look
    app.setStyleSheet("""
        QMainWindow { background: #2b2b2b; }
        QWidget { background: #2b2b2b; color: #ddd; font-size: 13px; }
        QListWidget { background: #1e1e1e; border: 1px solid #444; }
        QListWidget::item { padding: 4px; }
        QListWidget::item:selected { background: #3a6ea5; }
        QGroupBox { border: 1px solid #555; border-radius: 4px; margin-top: 8px; padding-top: 12px; font-weight: bold; }
        QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }
        QPushButton { background: #3a3a3a; border: 1px solid #555; border-radius: 4px; padding: 6px 12px; }
        QPushButton:hover { background: #4a4a4a; }
        QPushButton:pressed { background: #2a2a2a; }
        QPushButton:checked { background: #3a6ea5; border-color: #5a8ec5; }
        QPushButton:disabled { color: #666; }
        QToolBar { background: #333; border-bottom: 1px solid #444; spacing: 4px; padding: 4px; }
        QStatusBar { background: #333; border-top: 1px solid #444; }
        QProgressDialog { background: #2b2b2b; }
    """)

    window = MainWindow()
    window.show()

    try:
        sys.exit(app.exec())
    except (SystemExit, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
