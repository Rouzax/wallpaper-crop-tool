"""
Interactive crop-overlay widget and Qt image helpers.

This module contains everything that touches both Qt **and** image display:
``pil_to_qpixmap``, ``load_pixmap``, the background ``ImageLoaderThread``,
and the main ``ImageCropWidget`` editor.
"""

from pathlib import Path

from PIL import Image
from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QThread
from PyQt6.QtGui import (
    QPainter, QPixmap, QColor, QPen, QBrush, QImage,
    QKeyEvent, QMouseEvent, QPaintEvent, QResizeEvent,
)

from wallpaper_crop_tool.config import HANDLE_SIZE, MIN_CROP_SIZE, NUDGE_SMALL, NUDGE_LARGE
from wallpaper_crop_tool.models import CropRect, clamp_crop
from wallpaper_crop_tool.image_io import open_image


# =============================================================================
# Qt ↔ PIL helpers
# =============================================================================

def pil_to_qpixmap(pil_img: Image.Image) -> QPixmap:
    """Convert a PIL Image to QPixmap."""
    img_rgb = pil_img.convert("RGBA")
    data = img_rgb.tobytes("raw", "RGBA")
    qimg = QImage(data, img_rgb.width, img_rgb.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimg)


def load_pixmap(path: Path, fingerprint: str = "") -> QPixmap:
    """Load a QPixmap from any supported image file."""
    if path.suffix.lower() in (".psd", ".ai"):
        pil_img = open_image(path, fingerprint=fingerprint)
        return pil_to_qpixmap(pil_img)
    return QPixmap(str(path))


# =============================================================================
# Background image loader
# =============================================================================

class ImageLoaderThread(QThread):
    """Background thread for loading/compositing images (especially large PSDs)."""
    finished = pyqtSignal(QPixmap)
    error = pyqtSignal(str)

    def __init__(self, path: Path, parent=None, fingerprint: str = ""):
        super().__init__(parent)
        self._path = path
        self._fingerprint = fingerprint

    def run(self):
        try:
            pixmap = load_pixmap(self._path, fingerprint=self._fingerprint)
            self.finished.emit(pixmap)
        except Exception as e:
            self.error.emit(str(e))


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

        # Logo overlay
        self._logo_pixmap: QPixmap | None = None
        self._logo_config: dict | None = None  # position, size_percent, base_dimension, margin_px, target_w, target_h

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

    def has_image(self) -> bool:
        """Return True if an image is loaded and ready for crop operations."""
        return self._pixmap is not None

    def clear(self):
        self._pixmap = None
        self._img_w = 0
        self._img_h = 0
        self._crop = CropRect()
        self.update()

    def set_logo(self, pixmap: QPixmap | None, config: dict | None):
        """Set or clear the logo overlay.

        config keys: position, size_percent, base_dimension, margin_px, target_w, target_h
        """
        self._logo_pixmap = pixmap
        self._logo_config = config
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

        # Draw logo overlay inside crop area
        self._paint_logo_overlay(painter, crop_rect)

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

    def _paint_logo_overlay(self, painter: QPainter, crop_rect: QRectF):
        """Draw the logo preview inside the crop area."""
        if not self._logo_pixmap or not self._logo_config or self._logo_pixmap.isNull():
            return

        cfg = self._logo_config
        cr_w = crop_rect.width()
        cr_h = crop_rect.height()

        # Calculate logo display size (same logic as export)
        if cfg["base_dimension"] == "Width":
            basis = cr_w
        elif cfg["base_dimension"] == "Height":
            basis = cr_h
        else:  # Shorter side
            basis = min(cr_w, cr_h)
        logo_w_disp = max(1, basis * cfg["size_percent"] / 100.0)
        logo_aspect = self._logo_pixmap.height() / max(1, self._logo_pixmap.width())
        logo_h_disp = logo_w_disp * logo_aspect

        # Calculate margin in display space
        if cfg.get("margin_auto"):
            # Auto: margin = logo_height * ratio (in display coords)
            margin = logo_h_disp * cfg.get("margin_ratio", 0.75)
        else:
            # Fixed: scale from target px to display coords
            scale_to_disp = cr_w / max(1, cfg["target_w"])
            margin = cfg["margin_px"] * scale_to_disp

        # Position
        pos = cfg["position"]
        if pos == "TopLeft":
            lx = crop_rect.left() + margin
            ly = crop_rect.top() + margin
        elif pos == "TopRight":
            lx = crop_rect.right() - logo_w_disp - margin
            ly = crop_rect.top() + margin
        elif pos == "BottomLeft":
            lx = crop_rect.left() + margin
            ly = crop_rect.bottom() - logo_h_disp - margin
        elif pos == "BottomRight":
            lx = crop_rect.right() - logo_w_disp - margin
            ly = crop_rect.bottom() - logo_h_disp - margin
        else:  # Center
            lx = crop_rect.left() + (cr_w - logo_w_disp) / 2
            ly = crop_rect.top() + (cr_h - logo_h_disp) / 2

        # Clip to crop area and draw
        painter.save()
        painter.setClipRect(crop_rect)
        painter.setOpacity(0.9)
        logo_rect = QRectF(lx, ly, logo_w_disp, logo_h_disp)
        painter.drawPixmap(logo_rect.toRect(), self._logo_pixmap)
        painter.restore()

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
