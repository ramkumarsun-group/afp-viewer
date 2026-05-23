#!/usr/bin/env python3
"""
AFP Viewer — entry point.

Usage:
  python main.py [file.afp]
"""

import sys
import ctypes
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from src.gui import MainWindow


def _set_process_name(name: str) -> None:
    """Rename the process so macOS menu bar shows 'AFP Viewer' not 'Python'."""
    try:
        libc = ctypes.cdll.LoadLibrary('libSystem.B.dylib')
        libc.pthread_setname_np(name.encode())
    except Exception:
        pass


def main() -> None:
    _set_process_name('AFP Viewer')
    app = QApplication(sys.argv)
    app.setApplicationName('AFP Viewer')
    app.setApplicationDisplayName('AFP Viewer')
    app.setOrganizationName('AFPViewer')

    window = MainWindow()
    window.show()

    if len(sys.argv) > 1:
        window.load_file(sys.argv[1])

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
