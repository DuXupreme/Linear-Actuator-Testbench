"""Launch the Actuator Testbench GUI."""
from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Actuator Testbench")
    app.setOrganizationName("Gyrari")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

