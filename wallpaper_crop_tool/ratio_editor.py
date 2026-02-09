"""
Ratio editor dialog — two-panel layout.

Left panel shows ratio groups (one per unique aspect ratio).
Right panel shows export targets for the selected group.

Takes a nested ratios list as input, returns the modified list
on accept via ``get_ratios()``.
"""

from copy import deepcopy

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QSpinBox, QLineEdit, QHeaderView, QAbstractItemView,
    QDialogButtonBox, QLabel, QWidget, QMessageBox, QListWidget,
    QListWidgetItem, QGroupBox, QSplitter,
)
from PyQt6.QtCore import Qt

from wallpaper_crop_tool.config import DEFAULT_RATIOS
from wallpaper_crop_tool.ratios import validate_folder_name, aspect_key

# Target table column indices
_COL_TARGET_W = 0
_COL_TARGET_H = 1
_COL_FOLDER = 2
_TARGET_COLUMNS = ["Target W", "Target H", "Folder"]

_STYLE_ERROR = "border: 2px solid #d32f2f;"
_STYLE_NORMAL = ""


# =============================================================================
# Add-ratio dialog (creates a new group with one target)
# =============================================================================
class _AddRatioDialog(QDialog):
    """Small dialog to collect ratio string + target width for a new group."""

    def __init__(self, existing_groups: list[dict], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add Ratio")
        self.setMinimumWidth(300)
        self._existing_groups = existing_groups

        layout = QVBoxLayout(self)

        # Ratio input
        layout.addWidget(QLabel("Aspect ratio (e.g. 21:9):"))
        self._ratio_input = QLineEdit()
        self._ratio_input.setPlaceholderText("21:9")
        self._ratio_input.textChanged.connect(self._validate)
        layout.addWidget(self._ratio_input)

        # Target width input
        layout.addWidget(QLabel("Target width (px):"))
        self._width_input = QSpinBox()
        self._width_input.setRange(1, 99999)
        self._width_input.setValue(3840)
        layout.addWidget(self._width_input)

        # Error label
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #d32f2f;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        # Buttons
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._validate()

    def _validate(self):
        ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        parsed = self._parse_ratio()
        if parsed is None:
            self._error_label.setText("Enter ratio as W:H (e.g. 21:9)")
            ok_btn.setEnabled(False)
            return

        # Check for duplicate normalized aspect ratio
        rw, rh = parsed
        new_key = aspect_key(rw, rh)
        for g in self._existing_groups:
            existing_key = aspect_key(g["ratio_w"], g["ratio_h"])
            if new_key == existing_key:
                self._error_label.setText(
                    f"{rw}:{rh} is the same aspect ratio as {g['name']}, "
                    f"add a target there instead."
                )
                ok_btn.setEnabled(False)
                return

        self._error_label.setText("")
        ok_btn.setEnabled(True)

    def _parse_ratio(self) -> tuple[int, int] | None:
        text = self._ratio_input.text().strip()
        if ":" not in text:
            return None
        parts = text.split(":")
        if len(parts) != 2:
            return None
        try:
            w, h = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        if w <= 0 or h <= 0:
            return None
        return w, h

    def get_group(self) -> dict | None:
        """Return a new ratio group dict with one target, or None if invalid."""
        parsed = self._parse_ratio()
        if parsed is None:
            return None
        ratio_w, ratio_h = parsed
        target_w = self._width_input.value()
        target_h = int(round(target_w * ratio_h / ratio_w))
        name = f"{ratio_w}:{ratio_h}"
        folder = f"Ratio {ratio_w}x{ratio_h}"
        return {
            "name": name,
            "ratio_w": ratio_w,
            "ratio_h": ratio_h,
            "targets": [
                {"target_w": target_w, "target_h": target_h, "folder": folder},
            ],
        }


# =============================================================================
# Add-target dialog (adds a target to an existing group)
# =============================================================================
class _AddTargetDialog(QDialog):
    """Small dialog to collect a target width; height is auto-computed."""

    def __init__(self, ratio_w: int, ratio_h: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add Target")
        self.setMinimumWidth(280)
        self._ratio_w = ratio_w
        self._ratio_h = ratio_h

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"New target for {ratio_w}:{ratio_h}"))

        layout.addWidget(QLabel("Target width (px):"))
        self._width_input = QSpinBox()
        self._width_input.setRange(0, 99999)
        self._width_input.setValue(0)
        self._width_input.setSpecialValueText(" ")  # show blank when 0
        self._width_input.valueChanged.connect(self._validate)
        layout.addWidget(self._width_input)

        self._preview_label = QLabel("")
        self._preview_label.setStyleSheet("color: #888;")
        layout.addWidget(self._preview_label)

        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._validate()

    def _validate(self):
        tw = self._width_input.value()
        ok_btn = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if tw <= 0:
            self._preview_label.setText("")
            ok_btn.setEnabled(False)
        else:
            th = int(round(tw * self._ratio_h / self._ratio_w))
            self._preview_label.setText(f"Resolution: {tw}×{th}")
            ok_btn.setEnabled(True)

    def get_target(self) -> dict:
        """Return a target dict with auto-computed height and default folder."""
        tw = self._width_input.value()
        th = int(round(tw * self._ratio_h / self._ratio_w))
        return {
            "target_w": tw,
            "target_h": th,
            "folder": f"Ratio {self._ratio_w}x{self._ratio_h} {tw}x{th}",
        }


# =============================================================================
# Main editor dialog — two-panel layout
# =============================================================================
class RatioEditorDialog(QDialog):
    """Two-panel dialog for editing ratio groups and their export targets."""

    def __init__(self, current_ratios: list[dict], parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Edit Aspect Ratios")
        self.setMinimumSize(800, 420)

        self._groups: list[dict] = deepcopy(current_ratios)
        self._selected_group_idx: int = -1
        self._build_ui()
        self._populate_groups()
        self._validate()

    # -----------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter, stretch=1)

        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([260, 540])

        # Error label
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #d32f2f;")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        # OK / Cancel
        self._button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._button_box.accepted.connect(self.accept)
        self._button_box.rejected.connect(self.reject)
        layout.addWidget(self._button_box)

    def _build_left_panel(self) -> QWidget:
        group_box = QGroupBox("Ratio Groups")
        layout = QVBoxLayout(group_box)

        self._group_list = QListWidget()
        self._group_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._group_list.currentRowChanged.connect(self._on_group_selected)
        layout.addWidget(self._group_list)

        btn_row = QHBoxLayout()

        btn_add = QPushButton("➕ Add Ratio")
        btn_add.clicked.connect(self._on_add_group)
        btn_row.addWidget(btn_add)

        btn_remove = QPushButton("🗑 Remove")
        btn_remove.clicked.connect(self._on_remove_group)
        btn_row.addWidget(btn_remove)

        layout.addLayout(btn_row)

        order_row = QHBoxLayout()

        btn_up = QPushButton("⬆ Up")
        btn_up.clicked.connect(self._on_move_group_up)
        order_row.addWidget(btn_up)

        btn_down = QPushButton("⬇ Down")
        btn_down.clicked.connect(self._on_move_group_down)
        order_row.addWidget(btn_down)

        order_row.addStretch()

        btn_reset = QPushButton("↩ Reset")
        btn_reset.setToolTip("Reset to built-in defaults")
        btn_reset.clicked.connect(self._on_reset)
        order_row.addWidget(btn_reset)

        layout.addLayout(order_row)

        return group_box

    def _build_right_panel(self) -> QWidget:
        group_box = QGroupBox("Targets")
        layout = QVBoxLayout(group_box)

        self._targets_label = QLabel("Select a ratio group on the left.")
        self._targets_label.setStyleSheet("color: #888;")
        layout.addWidget(self._targets_label)

        self._target_table = QTableWidget(0, len(_TARGET_COLUMNS))
        self._target_table.setHorizontalHeaderLabels(_TARGET_COLUMNS)
        self._target_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._target_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._target_table.horizontalHeader().setStretchLastSection(True)
        self._target_table.horizontalHeader().setSectionResizeMode(
            _COL_TARGET_W, QHeaderView.ResizeMode.ResizeToContents
        )
        self._target_table.horizontalHeader().setSectionResizeMode(
            _COL_TARGET_H, QHeaderView.ResizeMode.ResizeToContents
        )
        self._target_table.verticalHeader().setVisible(False)
        layout.addWidget(self._target_table)

        btn_row = QHBoxLayout()

        btn_add_target = QPushButton("➕ Add Target")
        btn_add_target.clicked.connect(self._on_add_target)
        btn_row.addWidget(btn_add_target)

        btn_remove_target = QPushButton("🗑 Remove Target")
        btn_remove_target.clicked.connect(self._on_remove_target)
        btn_row.addWidget(btn_remove_target)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        return group_box

    # -----------------------------------------------------------------
    # Populate left panel from self._groups
    # -----------------------------------------------------------------
    def _populate_groups(self):
        """Rebuild the group list from self._groups."""
        self._group_list.blockSignals(True)
        self._group_list.clear()
        for g in self._groups:
            n = len(g.get("targets", []))
            label = g["name"] if n <= 1 else f"{g['name']} (×{n})"
            self._group_list.addItem(label)
        self._group_list.blockSignals(False)

        # Reset tracked index before triggering selection
        self._selected_group_idx = -1

        if self._groups:
            self._group_list.setCurrentRow(0)
        else:
            self._clear_targets()

    def _update_group_label(self, idx: int):
        """Update a single group list item label."""
        if idx < 0 or idx >= len(self._groups):
            return
        g = self._groups[idx]
        n = len(g.get("targets", []))
        label = g["name"] if n <= 1 else f"{g['name']} (×{n})"
        item = self._group_list.item(idx)
        if item:
            item.setText(label)

    # -----------------------------------------------------------------
    # Populate right panel from selected group
    # -----------------------------------------------------------------
    def _on_group_selected(self, row: int):
        """Selection sync: populate targets for the selected group."""
        self._save_targets()  # saves to _selected_group_idx (previous group)
        self._selected_group_idx = row  # NOW update to new selection
        if row < 0 or row >= len(self._groups):
            self._clear_targets()
            return
        g = self._groups[row]
        self._targets_label.setText(
            f"Targets for {g['name']} ({g['ratio_w']}:{g['ratio_h']})"
        )
        self._populate_targets(g.get("targets", []))

    def _populate_targets(self, targets: list[dict]):
        """Replace target table contents with the given targets list."""
        self._target_table.blockSignals(True)
        self._target_table.setRowCount(0)
        for t in targets:
            self._append_target_row(t)
        self._target_table.blockSignals(False)
        self._validate()

    def _clear_targets(self):
        """Clear the target table and label."""
        self._target_table.setRowCount(0)
        self._targets_label.setText("Select a ratio group on the left.")
        self._validate()

    def _append_target_row(self, t: dict):
        """Add one target as a new table row."""
        row = self._target_table.rowCount()
        self._target_table.insertRow(row)

        # Target W (QSpinBox)
        sw = QSpinBox()
        sw.setRange(1, 99999)
        sw.setValue(t.get("target_w", 1))
        sw.setFrame(False)
        sw.valueChanged.connect(self._on_target_edited)
        self._target_table.setCellWidget(row, _COL_TARGET_W, sw)

        # Target H (QSpinBox)
        sh = QSpinBox()
        sh.setRange(1, 99999)
        sh.setValue(t.get("target_h", 1))
        sh.setFrame(False)
        sh.valueChanged.connect(self._on_target_edited)
        self._target_table.setCellWidget(row, _COL_TARGET_H, sh)

        # Folder (QLineEdit)
        le = QLineEdit(t.get("folder", ""))
        le.setFrame(False)
        le.textChanged.connect(self._on_target_edited)
        self._target_table.setCellWidget(row, _COL_FOLDER, le)

    def _read_target_row(self, row: int) -> dict:
        """Read a single target row into a dict."""
        w_widget = self._target_table.cellWidget(row, _COL_TARGET_W)
        h_widget = self._target_table.cellWidget(row, _COL_TARGET_H)
        f_widget = self._target_table.cellWidget(row, _COL_FOLDER)
        return {
            "target_w": w_widget.value() if isinstance(w_widget, QSpinBox) else 0,
            "target_h": h_widget.value() if isinstance(h_widget, QSpinBox) else 0,
            "folder": f_widget.text().strip() if isinstance(f_widget, QLineEdit) else "",
        }

    def _read_all_targets(self) -> list[dict]:
        """Read all target rows from the table."""
        return [self._read_target_row(r) for r in range(self._target_table.rowCount())]

    def _on_target_edited(self, *_args):
        """Called when any target cell is edited."""
        self._save_targets()
        self._validate()

    def _save_targets(self):
        """Write current target table contents back into the tracked group."""
        idx = self._selected_group_idx
        if idx < 0 or idx >= len(self._groups):
            return
        if self._target_table.rowCount() == 0:
            return  # nothing to save (cleared state)
        self._groups[idx]["targets"] = self._read_all_targets()
        self._update_group_label(idx)

    # -----------------------------------------------------------------
    # Output
    # -----------------------------------------------------------------
    def get_ratios(self) -> list[dict]:
        """Return the current state as the nested ratios list format."""
        self._save_targets()
        return deepcopy(self._groups)

    # -----------------------------------------------------------------
    # Validation
    # -----------------------------------------------------------------
    def _validate(self):
        """Run live validation across all groups and targets."""
        errors: list[str] = []

        # Ensure target table is saved to self._groups
        idx = self._selected_group_idx
        if 0 <= idx < len(self._groups) and self._target_table.rowCount() > 0:
            self._groups[idx]["targets"] = self._read_all_targets()

        # Reset all target cell styles
        for row in range(self._target_table.rowCount()):
            for col in range(len(_TARGET_COLUMNS)):
                w = self._target_table.cellWidget(row, col)
                if w:
                    w.setStyleSheet(_STYLE_NORMAL)

        # Collect all folders across all groups for duplicate detection
        all_folders: dict[str, str] = {}  # folder -> location string

        for gi, g in enumerate(self._groups):
            targets = g.get("targets", [])
            if not targets:
                errors.append(f"'{g['name']}' has no targets")
                continue

            for ti, t in enumerate(targets):
                location = f"'{g['name']}' target #{ti + 1}"
                folder = t.get("folder", "").strip()

                # Validate folder name
                folder_err = validate_folder_name(folder) if folder else "folder must be a non-empty string"
                if folder_err:
                    errors.append(f"{location}: {folder_err}")
                    # Highlight if this is the currently visible group
                    if gi == idx and ti < self._target_table.rowCount():
                        w = self._target_table.cellWidget(ti, _COL_FOLDER)
                        if w:
                            w.setStyleSheet(_STYLE_ERROR)
                elif folder in all_folders:
                    errors.append(
                        f"{location}: duplicate folder '{folder}' "
                        f"(also used by {all_folders[folder]})"
                    )
                    if gi == idx and ti < self._target_table.rowCount():
                        w = self._target_table.cellWidget(ti, _COL_FOLDER)
                        if w:
                            w.setStyleSheet(_STYLE_ERROR)
                else:
                    all_folders[folder] = location

        # Update UI
        ok_btn = self._button_box.button(QDialogButtonBox.StandardButton.Ok)
        if errors:
            self._error_label.setText("; ".join(errors))
            ok_btn.setEnabled(False)
        else:
            self._error_label.setText("")
            ok_btn.setEnabled(True)

    # -----------------------------------------------------------------
    # Group actions (left panel)
    # -----------------------------------------------------------------
    def _on_add_group(self):
        dlg = _AddRatioDialog(self._groups, parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        group = dlg.get_group()
        if group is None:
            return

        self._save_targets()
        self._groups.append(group)
        self._group_list.addItem(group["name"])
        self._group_list.setCurrentRow(len(self._groups) - 1)
        self._validate()

    def _on_remove_group(self):
        row = self._group_list.currentRow()
        if row < 0:
            return

        g = self._groups[row]
        n_targets = len(g.get("targets", []))
        if n_targets > 0:
            reply = QMessageBox.question(
                self,
                "Remove Ratio Group",
                f"Remove '{g['name']}' and its {n_targets} target(s)?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._groups.pop(row)
        self._group_list.takeItem(row)

        # Reset tracked index — the removed group's data is gone
        self._selected_group_idx = -1

        # Select nearest remaining item
        if self._groups:
            new_row = min(row, len(self._groups) - 1)
            self._group_list.setCurrentRow(new_row)
        else:
            self._clear_targets()

        self._validate()

    def _on_move_group_up(self):
        row = self._group_list.currentRow()
        if row <= 0:
            return
        self._save_targets()
        self._groups[row], self._groups[row - 1] = self._groups[row - 1], self._groups[row]
        self._populate_groups()
        self._group_list.setCurrentRow(row - 1)

    def _on_move_group_down(self):
        row = self._group_list.currentRow()
        if row < 0 or row >= len(self._groups) - 1:
            return
        self._save_targets()
        self._groups[row], self._groups[row + 1] = self._groups[row + 1], self._groups[row]
        self._populate_groups()
        self._group_list.setCurrentRow(row + 1)

    def _on_reset(self):
        reply = QMessageBox.question(
            self,
            "Reset to Defaults",
            "Replace all ratios with the built-in defaults?\n\n"
            "This will discard any custom ratios.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._groups = deepcopy(DEFAULT_RATIOS)
            self._populate_groups()
            self._validate()

    # -----------------------------------------------------------------
    # Target actions (right panel)
    # -----------------------------------------------------------------
    def _on_add_target(self):
        idx = self._group_list.currentRow()
        if idx < 0 or idx >= len(self._groups):
            return
        g = self._groups[idx]

        dlg = _AddTargetDialog(g["ratio_w"], g["ratio_h"], parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target = dlg.get_target()

        self._save_targets()
        self._groups[idx]["targets"].append(target)
        self._append_target_row(target)
        self._target_table.setCurrentCell(self._target_table.rowCount() - 1, 0)
        self._update_group_label(idx)
        self._validate()

    def _on_remove_target(self):
        idx = self._group_list.currentRow()
        if idx < 0 or idx >= len(self._groups):
            return

        trow = self._target_table.currentRow()
        if trow < 0:
            return

        # Save current table to group data, then remove the target
        self._save_targets()
        targets = self._groups[idx].get("targets", [])

        if len(targets) <= 1:
            # Last target — auto-delete the group
            reply = QMessageBox.question(
                self,
                "Remove Last Target",
                f"This is the only target in '{self._groups[idx]['name']}'.\n"
                f"Removing it will delete the entire ratio group.\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
            self._groups.pop(idx)
            self._group_list.takeItem(idx)
            self._selected_group_idx = -1
            if self._groups:
                new_row = min(idx, len(self._groups) - 1)
                self._group_list.setCurrentRow(new_row)
            else:
                self._clear_targets()
        else:
            # Remove just this target
            targets.pop(trow)
            self._groups[idx]["targets"] = targets
            self._target_table.removeRow(trow)
            self._update_group_label(idx)

        self._validate()
