"""Bounded rolling telemetry graphs using pyqtgraph."""
from __future__ import annotations

from collections import deque
import time
from typing import Callable

import pyqtgraph as pg
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from .telemetry import Telemetry


class GraphPanel(QWidget):
    MAX_POINTS = 6500  # >120 seconds at 50 Hz, bounded regardless of run time

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.paused = False
        self.start_time: float | None = None
        self.data = {name: deque(maxlen=self.MAX_POINTS) for name in (
            "time", "target", "command", "position", "error", "pwm", "current", "voltage", "power"
        )}
        root = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.window_combo = QComboBox(); self.window_combo.addItems(["5", "10", "30", "60", "120"]); self.window_combo.setCurrentText("30")
        self.pause_button = QPushButton("Pause"); self.pause_button.clicked.connect(self.toggle_pause)
        clear = QPushButton("Clear"); clear.clicked.connect(self.clear)
        auto = QPushButton("Auto range"); auto.clicked.connect(self.auto_range)
        self.ymin = QDoubleSpinBox(); self.ymin.setRange(-10000, 10000); self.ymin.setValue(-10)
        self.ymax = QDoubleSpinBox(); self.ymax.setRange(-10000, 10000); self.ymax.setValue(100)
        manual = QPushButton("Set Y"); manual.clicked.connect(self.manual_range)
        for widget in (QLabel("Window (s)"), self.window_combo, self.pause_button, clear, auto,
                       QLabel("Y min"), self.ymin, QLabel("Y max"), self.ymax, manual):
            tools.addWidget(widget)
        root.addLayout(tools)
        self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self.plots: dict[str, pg.PlotWidget] = {}
        self.curves: dict[str, pg.PlotDataItem] = {}
        self._add_plot("Position", [("target", "Target", "#ffd166"), ("command", "Command pot", "#a78bfa"), ("position", "Actual", "#06d6a0")], "%")
        self._add_plot("Error", [("error", "Error", "#ef476f")], "%")
        self._add_plot("PWM", [("pwm", "PWM", "#4cc9f0")], "PWM")
        self._add_plot("Current", [("current", "Current", "#ff9f1c")], "A")
        self._add_plot("Bus voltage", [("voltage", "Bus voltage", "#80ed99")], "V")
        self._add_plot("Power", [("power", "Power", "#e0aaff")], "W")
        visibility = QHBoxLayout(); visibility.addWidget(QLabel("Traces:"))
        for key in self.curves:
            box = QCheckBox(key.replace("_", " ").title()); box.setChecked(True)
            box.toggled.connect(self.curves[key].setVisible); visibility.addWidget(box)
        visibility.addStretch(); self.cursor_label = QLabel("Cursor: —"); visibility.addWidget(self.cursor_label)
        root.addLayout(visibility)
        self.timer = QTimer(self); self.timer.timeout.connect(self.refresh); self.timer.start(50)

    def _add_plot(self, title: str, traces: list[tuple[str, str, str]], unit: str) -> None:
        plot = pg.PlotWidget(); plot.setBackground("#14181f"); plot.showGrid(x=True, y=True, alpha=.25)
        plot.setLabel("bottom", "Time", units="s"); plot.setLabel("left", title, units=unit)
        plot.addLegend(); self.tabs.addTab(plot, title); self.plots[title] = plot
        for key, label, color in traces:
            self.curves[key] = plot.plot([], [], name=label, pen=pg.mkPen(color, width=2))
        proxy = pg.SignalProxy(plot.scene().sigMouseMoved, rateLimit=30, slot=lambda event, p=plot: self._mouse(event, p))
        plot._cursor_proxy = proxy  # keep proxy alive

    def add(self, t: Telemetry) -> None:
        if self.paused:
            return
        if self.start_time is None:
            self.start_time = t.pc_time or time.time()
        x = (t.pc_time or time.time()) - self.start_time
        values = (x, t.target_pct, t.command_pct, t.feedback_pct, t.error_pct, t.pwm,
                  t.current_a, t.bus_voltage_v, t.power_w)
        for key, value in zip(self.data, values):
            self.data[key].append(value)

    def refresh(self) -> None:
        if not self.data["time"]:
            return
        xs = list(self.data["time"]); end = xs[-1]; start = end - float(self.window_combo.currentText())
        first = 0
        while first < len(xs) and xs[first] < start:
            first += 1
        xview = xs[first:]
        for key, curve in self.curves.items():
            curve.setData(xview, list(self.data[key])[first:])
        for plot in self.plots.values():
            plot.setXRange(max(0, start), max(float(self.window_combo.currentText()), end), padding=0)

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        self.pause_button.setText("Resume" if self.paused else "Pause")

    def clear(self) -> None:
        for values in self.data.values(): values.clear()
        self.start_time = None
        for curve in self.curves.values(): curve.setData([], [])

    def auto_range(self) -> None:
        self.current_plot().enableAutoRange(axis="y")

    def manual_range(self) -> None:
        if self.ymax.value() > self.ymin.value():
            self.current_plot().setYRange(self.ymin.value(), self.ymax.value(), padding=0)

    def current_plot(self) -> pg.PlotWidget:
        return self.tabs.currentWidget()  # type: ignore[return-value]

    def _mouse(self, event: tuple[object], plot: pg.PlotWidget) -> None:
        point = event[0]
        if plot.sceneBoundingRect().contains(point):
            mapped = plot.plotItem.vb.mapSceneToView(point)
            self.cursor_label.setText(f"Cursor: {mapped.x():.2f} s, {mapped.y():.3f}")

