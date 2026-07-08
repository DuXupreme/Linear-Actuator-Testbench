from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel


class ValueLabel(QLabel):
    """Large fixed-width readout used for safety-relevant live values."""

    def __init__(self, text: str = "—", color: str = "#e7edf5") -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumWidth(115)
        self.setStyleSheet(
            f"font-size: 22px; font-weight: 700; color: {color}; "
            "background: #1b222c; border: 1px solid #3b4655; padding: 7px;"
        )

