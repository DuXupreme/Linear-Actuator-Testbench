"""Background CSV telemetry logger with a JSON configuration sidecar."""
from __future__ import annotations

import csv
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
from queue import Empty, Queue
import threading
from typing import Any

from .telemetry import Telemetry


class TelemetryLogger:
    def __init__(self) -> None:
        self._queue: Queue[Telemetry | None] = Queue(maxsize=10000)
        self._thread: threading.Thread | None = None
        self.active = False
        self.path: Path | None = None
        self.dropped = 0

    def start(self, directory: str | Path, metadata: dict[str, Any]) -> Path:
        if self.active:
            raise RuntimeError("Logging is already active")
        folder = Path(directory); folder.mkdir(parents=True, exist_ok=True)
        stem = datetime.now().strftime("actuator_%Y-%m-%d_%H-%M-%S")
        self.path = folder / f"{stem}.csv"
        (folder / f"{stem}.json").write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
        self.active = True; self.dropped = 0
        self._thread = threading.Thread(target=self._writer, args=(self.path,), daemon=True)
        self._thread.start(); return self.path

    def add(self, telemetry: Telemetry) -> None:
        if not self.active:
            return
        try:
            self._queue.put_nowait(telemetry)
        except Exception:
            self.dropped += 1

    def stop(self) -> None:
        if not self.active:
            return
        self.active = False
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=3)
        self._thread = None

    def _writer(self, path: Path) -> None:
        fields = [
            "pc_timestamp", "arduino_time_ms", "mode", "command_position_pct", "target_position_pct",
            "actual_position_pct", "error_pct", "pwm", "current_a", "filtered_current_a",
            "peak_current_a", "bus_voltage_v", "shunt_voltage_mv", "power_w", "fault_code",
            "fault_text", "fault_latched", "lower_limit", "upper_limit", "estop",
        ]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
            while True:
                item = self._queue.get()
                if item is None:
                    break
                writer.writerow({
                    "pc_timestamp": datetime.fromtimestamp(item.pc_time).isoformat(timespec="milliseconds"),
                    "arduino_time_ms": item.time_ms, "mode": item.mode,
                    "command_position_pct": item.command_pct, "target_position_pct": item.target_pct,
                    "actual_position_pct": item.feedback_pct, "error_pct": item.error_pct,
                    "pwm": item.pwm, "current_a": item.current_a,
                    "filtered_current_a": item.filtered_current_a, "peak_current_a": item.peak_current_a,
                    "bus_voltage_v": item.bus_voltage_v, "shunt_voltage_mv": item.shunt_voltage_mv,
                    "power_w": item.power_w, "fault_code": item.fault_code,
                    "fault_text": item.fault_text, "fault_latched": int(item.fault_latched),
                    "lower_limit": int(item.lower_limit), "upper_limit": int(item.upper_limit),
                    "estop": int(item.estop),
                })
                if self._queue.empty(): stream.flush()

