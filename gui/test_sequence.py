"""Non-blocking, GUI-supervised step response sequence and metrics."""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from .telemetry import Telemetry


@dataclass(slots=True)
class StepSettings:
    start_pct: float = 25.0
    end_pct: float = 75.0
    delay_s: float = 1.0
    hold_s: float = 3.0
    repetitions: int = 1
    max_current_a: float = 8.0
    max_duration_s: float = 30.0
    return_to_start: bool = True
    tolerance_pct: float = 1.0


class StepSequence(QObject):
    status_changed = Signal(str)
    finished = Signal(dict)
    aborted = Signal(str)

    def __init__(self, send: Callable[[str], None], parent: QObject | None = None) -> None:
        super().__init__(parent); self.send = send
        self.timer = QTimer(self); self.timer.setInterval(20); self.timer.timeout.connect(self._tick)
        self.settings = StepSettings(); self.latest: Telemetry | None = None
        self.running = False; self.state = "IDLE"; self.state_since = 0.0; self.started = 0.0
        self.repetition = 0; self.samples: list[Telemetry] = []; self.step_time = 0.0

    def start(self, settings: StepSettings, latest: Telemetry | None) -> None:
        if self.running: raise RuntimeError("A step test is already running")
        if latest is None: raise RuntimeError("No telemetry received")
        if latest.fault_code: raise RuntimeError(f"Active fault: {latest.fault_text}")
        self.settings = settings; self.latest = latest; self.samples = []; self.repetition = 0
        self.running = True; self.started = time.monotonic(); self._enter("MOVE_START")
        self.send("CMD,MODE,STEP"); self.send(f"CMD,TARGET,{settings.start_pct:.3f}"); self.timer.start()

    def on_telemetry(self, telemetry: Telemetry) -> None:
        self.latest = telemetry
        if self.running: self.samples.append(telemetry)

    def abort(self, reason: str = "Operator abort") -> None:
        if not self.running: return
        self.running = False; self.timer.stop(); self.send("CMD,STOP"); self.aborted.emit(reason); self.status_changed.emit("ABORTED")

    def _enter(self, state: str) -> None:
        self.state = state; self.state_since = time.monotonic()
        shown = min(self.repetition + 1, self.settings.repetitions)
        self.status_changed.emit(f"{state} ({shown}/{self.settings.repetitions})")

    def _tick(self) -> None:
        if not self.running or self.latest is None: return
        now = time.monotonic(); t = self.latest; s = self.settings
        if now - self.started > s.max_duration_s: self.abort("Maximum test duration exceeded"); return
        if t.fault_code: self.abort(f"Firmware fault: {t.fault_text}"); return
        if t.current_a > s.max_current_a: self.abort("Test current limit exceeded"); return
        if self.state == "MOVE_START" and abs(t.feedback_pct-s.start_pct) <= s.tolerance_pct:
            self._enter("BASELINE")
        elif self.state == "BASELINE" and now-self.state_since >= s.delay_s:
            self.step_time = time.time(); self.send(f"CMD,TARGET,{s.end_pct:.3f}"); self._enter("STEP_HOLD")
        elif self.state == "STEP_HOLD" and now-self.state_since >= s.hold_s:
            self.repetition += 1
            if self.repetition < s.repetitions:
                self.send(f"CMD,TARGET,{s.start_pct:.3f}"); self._enter("MOVE_START_REPEAT")
            elif s.return_to_start:
                self.send(f"CMD,TARGET,{s.start_pct:.3f}"); self._enter("FINAL_RETURN")
            else:
                self._finish()
        elif self.state == "MOVE_START_REPEAT" and abs(t.feedback_pct-s.start_pct) <= s.tolerance_pct:
            self._enter("BASELINE")
        elif self.state == "FINAL_RETURN" and abs(t.feedback_pct-s.start_pct) <= s.tolerance_pct:
            self._finish()

    def _finish(self) -> None:
        self.running = False; self.timer.stop(); self.send("CMD,STOP")
        metrics = self._metrics(); self.finished.emit(metrics); self.status_changed.emit("COMPLETE")

    def _metrics(self) -> dict[str, float]:
        samples = [x for x in self.samples if x.pc_time >= self.step_time]
        if not samples: return {}
        s = self.settings; amplitude = s.end_pct-s.start_pct
        direction = 1 if amplitude >= 0 else -1; magnitude = abs(amplitude)
        def first_time(level: float) -> float | None:
            for item in samples:
                if direction*(item.feedback_pct-s.start_pct) >= level*magnitude:
                    return item.pc_time-self.step_time
            return None
        t10, t90 = first_time(.1), first_time(.9)
        peak = max(direction*(x.feedback_pct-s.end_pct) for x in samples)
        settling = math.nan
        for i, item in enumerate(samples):
            if all(abs(x.feedback_pct-s.end_pct) <= s.tolerance_pct for x in samples[i:]):
                settling = item.pc_time-self.step_time; break
        movement = first_time(max(.01, s.tolerance_pct/max(magnitude, .01)))
        return {
            "rise_time_s": (t90-t10) if t10 is not None and t90 is not None else math.nan,
            "settling_time_s": settling,
            "overshoot_pct": max(0.0, 100*peak/max(magnitude,.01)),
            "maximum_current_a": max(x.current_a for x in samples),
            "steady_state_error_pct": samples[-1].error_pct,
            "movement_start_delay_s": movement if movement is not None else math.nan,
            "raw_samples": float(len(samples)),
        }
