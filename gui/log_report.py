"""Create a permanent PNG dashboard from an entire telemetry CSV log."""
from __future__ import annotations

import csv
from datetime import datetime
import math
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF


COLORS = {
    "target_position_pct": QColor("#ffd166"),
    "command_position_pct": QColor("#a78bfa"),
    "actual_position_pct": QColor("#06d6a0"),
    "error_pct": QColor("#ef476f"),
    "pwm": QColor("#4cc9f0"),
    "current_a": QColor("#ff9f1c"),
    "filtered_current_a": QColor("#ffe066"),
    "bus_voltage_v": QColor("#80ed99"),
    "power_w": QColor("#e0aaff"),
}


def _number(value: str) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _read_log(path: Path) -> tuple[list[float], dict[str, list[float]]]:
    keys = list(COLORS)
    values = {key: [] for key in keys}
    times: list[float] = []
    first: datetime | None = None
    with path.open("r", newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            try:
                stamp = datetime.fromisoformat(row["pc_timestamp"])
            except (KeyError, ValueError):
                continue
            if first is None:
                first = stamp
            times.append((stamp - first).total_seconds())
            for key in keys:
                values[key].append(_number(row.get(key, "0")))
    if len(times) < 2:
        raise ValueError("The CSV log contains fewer than two telemetry samples")
    return times, values


def _range(series: Iterable[float], include_zero: bool = False) -> tuple[float, float]:
    data = list(series)
    low, high = min(data), max(data)
    if include_zero:
        low, high = min(0.0, low), max(0.0, high)
    if math.isclose(low, high):
        pad = max(1.0, abs(low) * 0.05)
    else:
        pad = (high - low) * 0.08
    return low - pad, high + pad


def _draw_panel(
    painter: QPainter,
    rect: QRectF,
    title: str,
    unit: str,
    times: list[float],
    values: dict[str, list[float]],
    traces: list[tuple[str, str]],
    y_range: tuple[float, float],
) -> None:
    painter.fillRect(rect, QColor("#141a22"))
    painter.setPen(QPen(QColor("#465568"), 1))
    painter.drawRect(rect)
    painter.setFont(QFont("Arial", 12, QFont.Weight.Bold))
    painter.setPen(QColor("#e6edf5"))
    painter.drawText(QPointF(rect.left() + 12, rect.top() + 22), f"{title} ({unit})")

    plot = QRectF(rect.left() + 62, rect.top() + 36, rect.width() - 78, rect.height() - 70)
    painter.setFont(QFont("Arial", 8))
    y_min, y_max = y_range
    duration = max(times[-1], 0.001)
    for index in range(5):
        fraction = index / 4
        y = plot.bottom() - fraction * plot.height()
        painter.setPen(QPen(QColor("#2d3948"), 1))
        painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        painter.setPen(QColor("#aeb9c6"))
        painter.drawText(QPointF(rect.left() + 5, y + 3), f"{y_min + fraction*(y_max-y_min):.2f}")
        x = plot.left() + fraction * plot.width()
        painter.setPen(QPen(QColor("#2d3948"), 1))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        painter.setPen(QColor("#aeb9c6"))
        painter.drawText(QPointF(x - 10, plot.bottom() + 16), f"{fraction*duration:.1f}")
    painter.drawText(QPointF(plot.right() - 32, plot.bottom() + 29), "time (s)")

    step = max(1, len(times) // 6000)
    for key, label in traces:
        points: list[QPointF] = []
        for i in range(0, len(times), step):
            x = plot.left() + times[i] / duration * plot.width()
            normalized = (values[key][i] - y_min) / max(y_max - y_min, 1e-12)
            y = plot.bottom() - normalized * plot.height()
            points.append(QPointF(x, y))
        painter.setPen(QPen(COLORS[key], 2))
        painter.drawPolyline(QPolygonF(points))

    legend_x = plot.left() + 8
    for key, label in traces:
        painter.setPen(QPen(COLORS[key], 3)); painter.drawLine(QPointF(legend_x, plot.top()+10), QPointF(legend_x+18, plot.top()+10))
        painter.setPen(QColor("#dce5ef")); painter.drawText(QPointF(legend_x+23, plot.top()+13), label)
        legend_x += 105


def save_log_graphs(csv_path: str | Path) -> Path:
    """Render all principal telemetry traces and return the PNG path."""
    source = Path(csv_path)
    times, values = _read_log(source)
    output = source.with_name(f"{source.stem}_graphs.png")
    image = QImage(1800, 1250, QImage.Format.Format_ARGB32)
    image.fill(QColor("#0f141b"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QColor("#edf4fb")); painter.setFont(QFont("Arial", 18, QFont.Weight.Bold))
    painter.drawText(QPointF(25, 32), f"Actuator Testbench log — {source.stem}")
    painter.setFont(QFont("Arial", 10)); painter.setPen(QColor("#aeb9c6"))
    painter.drawText(QPointF(25, 52), f"{len(times)} samples | {times[-1]:.2f} s | generated from complete CSV")

    margin, gap, top = 22.0, 18.0, 70.0
    width = (1800 - 2*margin - gap) / 2
    height = (1250 - top - margin - 2*gap) / 3
    panels = [
        ("Position", "%", [("target_position_pct","Target"),("command_position_pct","Command"),("actual_position_pct","Actual")], (0.0,100.0)),
        ("Position error", "%", [("error_pct","Error")], _range(values["error_pct"], True)),
        ("PWM command", "PWM", [("pwm","PWM")], (-255.0,255.0)),
        ("Supply current", "A", [("current_a","Current"),("filtered_current_a","Filtered")], _range(values["current_a"]+values["filtered_current_a"], True)),
        ("Bus voltage", "V", [("bus_voltage_v","Voltage")], _range(values["bus_voltage_v"])),
        ("Power", "W", [("power_w","Power")], _range(values["power_w"], True)),
    ]
    for index, (title, unit, traces, y_range) in enumerate(panels):
        row, col = divmod(index, 2)
        rect = QRectF(margin + col*(width+gap), top + row*(height+gap), width, height)
        _draw_panel(painter, rect, title, unit, times, values, traces, y_range)
    painter.end()
    if not image.save(str(output), "PNG"):
        raise OSError(f"Could not save graph image {output}")
    return output
