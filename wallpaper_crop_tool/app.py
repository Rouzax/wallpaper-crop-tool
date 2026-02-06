"""
Application entry point and dark-theme stylesheet.

Usage:
    python -m wallpaper_crop_tool
    wallpaper-crop-tool          (after pip install)
"""

import sys

from PyQt6.QtWidgets import QApplication

from wallpaper_crop_tool.main_window import MainWindow

DARK_STYLESHEET = """
    QMainWindow { background: #2b2b2b; }
    QWidget { background: #2b2b2b; color: #ddd; font-size: 10pt; }
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
"""


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    try:
        sys.exit(app.exec())
    except (SystemExit, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    main()
