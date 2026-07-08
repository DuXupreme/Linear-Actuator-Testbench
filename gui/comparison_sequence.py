"""Automated, non-blocking comparison of position-controller profiles.

The external feedback potentiometer is the reference for all dynamic metrics.
INA228 values are logged as supply-current/energy indicators; they are not
presented as true motor torque because the sensor is upstream of the H-bridge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from statistics import median
import time
from typing import Callable, Iterable

from PySide6.QtCore import QObject, QTimer, Signal

from .telemetry import Telemetry


PROFILE_PARAMETERS = (
    "KP", "KI", "KD", "DEADBAND", "MIN_PWM", "MAX_PWM", "PWM_SLEW",
    "DERIV_FILTER", "FEEDBACK_FILTER",
)


@dataclass(slots=True)
class ControllerProfile:
    name: str
    values: dict[str, float]


@dataclass(slots=True)
class ComparisonSettings:
    start_pct: float = 25.0
    end_pct: float = 75.0
    baseline_s: float = 1.0
    hold_s: float = 4.0
    repetitions: int = 3
    tolerance_pct: float = 1.0
    max_current_a: float = 8.0
    move_timeout_s: float = 20.0
    stable_before_step_s: float = 0.35


@dataclass(slots=True)
class RunMetrics:
    profile_name: str
    repetition: int
    rise_time_s: float
    settling_time_s: float
    overshoot_pct: float
    movement_delay_s: float
    steady_state_error_pct: float
    peak_current_a: float
    rms_current_a: float
    energy_j: float
    max_speed_pct_s: float
    max_acceleration_pct_s2: float
    samples: int


@dataclass(slots=True)
class ProfileResult:
    profile_name: str
    runs: list[RunMetrics] = field(default_factory=list)
    medians: dict[str, float] = field(default_factory=dict)
    score: float = math.inf
    rank: int = 0
    valid: bool = False


METRIC_NAMES = (
    "rise_time_s", "settling_time_s", "overshoot_pct", "movement_delay_s",
    "steady_state_error_pct", "peak_current_a", "rms_current_a", "energy_j",
    "max_speed_pct_s", "max_acceleration_pct_s2",
)


def _first_crossing(samples: list[Telemetry], start: float, direction: int,
                    distance: float, step_time: float) -> float:
    for item in samples:
        if direction * (item.feedback_pct - start) >= distance:
            return max(0.0, item.pc_time - step_time)
    return math.nan


def calculate_step_metrics(
    samples: Iterable[Telemetry],
    profile_name: str,
    repetition: int,
    start_pct: float,
    end_pct: float,
    step_time: float,
    tolerance_pct: float,
) -> RunMetrics:
    """Calculate auditable metrics from one externally measured position step."""
    ordered = sorted((item for item in samples if item.pc_time >= step_time), key=lambda x: x.pc_time)
    if len(ordered) < 3:
        raise ValueError("At least three telemetry samples are required for step metrics")
    amplitude = end_pct - start_pct
    magnitude = abs(amplitude)
    if magnitude < 0.1:
        raise ValueError("Start and end position must differ by at least 0.1%")
    direction = 1 if amplitude > 0 else -1

    t10 = _first_crossing(ordered, start_pct, direction, 0.10 * magnitude, step_time)
    t90 = _first_crossing(ordered, start_pct, direction, 0.90 * magnitude, step_time)
    rise = t90 - t10 if math.isfinite(t10) and math.isfinite(t90) else math.nan
    movement_threshold = max(tolerance_pct, 0.02 * magnitude)
    movement_delay = _first_crossing(
        ordered, start_pct, direction, movement_threshold, step_time
    )

    settling = math.nan
    # Require the signal to remain inside tolerance for the rest of the recorded
    # hold. A too-short hold therefore cannot falsely report a fast settling time.
    for index, item in enumerate(ordered):
        if all(abs(later.feedback_pct - end_pct) <= tolerance_pct for later in ordered[index:]):
            settling = max(0.0, item.pc_time - step_time)
            break

    signed_overshoot = max(direction * (item.feedback_pct - end_pct) for item in ordered)
    overshoot = max(0.0, 100.0 * signed_overshoot / magnitude)
    tail = ordered[max(0, len(ordered) - max(3, len(ordered) // 5)):]
    steady_error = sum(abs(end_pct - item.feedback_pct) for item in tail) / len(tail)
    peak_current = max(abs(item.current_a) for item in ordered)
    rms_current = math.sqrt(sum(item.current_a * item.current_a for item in ordered) / len(ordered))

    energy = 0.0
    raw_velocities: list[tuple[float, float]] = []
    for previous, current in zip(ordered, ordered[1:]):
        dt = current.pc_time - previous.pc_time
        if dt <= 0:
            continue
        energy += 0.5 * (abs(previous.power_w) + abs(current.power_w)) * dt
        raw_velocities.append((current.pc_time, (current.feedback_pct - previous.feedback_pct) / dt))

    # A three-sample moving average keeps ADC quantisation from dominating the
    # acceleration estimate while preserving the roughly 50 Hz response shape.
    velocities: list[tuple[float, float]] = []
    for index, (timestamp, _) in enumerate(raw_velocities):
        window = raw_velocities[max(0, index - 1):min(len(raw_velocities), index + 2)]
        velocities.append((timestamp, sum(value for _, value in window) / len(window)))
    max_speed = max((abs(value) for _, value in velocities), default=math.nan)
    accelerations: list[float] = []
    for previous, current in zip(velocities, velocities[1:]):
        dt = current[0] - previous[0]
        if dt > 0:
            accelerations.append((current[1] - previous[1]) / dt)
    max_acceleration = max((abs(value) for value in accelerations), default=math.nan)

    return RunMetrics(
        profile_name=profile_name,
        repetition=repetition,
        rise_time_s=rise,
        settling_time_s=settling,
        overshoot_pct=overshoot,
        movement_delay_s=movement_delay,
        steady_state_error_pct=steady_error,
        peak_current_a=peak_current,
        rms_current_a=rms_current,
        energy_j=energy,
        max_speed_pct_s=max_speed,
        max_acceleration_pct_s2=max_acceleration,
        samples=len(ordered),
    )


def aggregate_and_rank(runs: Iterable[RunMetrics]) -> list[ProfileResult]:
    """Aggregate repetitions and rank profiles with a transparent relative score.

    Score weights: settling 45%, rise 25%, overshoot 15%, peak current 10%,
    energy 5%. Each term is min/max-normalised inside the tested profile set;
    lower is better. A profile that never settles is ranked invalid and last.
    """
    grouped: dict[str, list[RunMetrics]] = {}
    for run in runs:
        grouped.setdefault(run.profile_name, []).append(run)
    results: list[ProfileResult] = []
    for name, profile_runs in grouped.items():
        medians = {
            metric: median(getattr(run, metric) for run in profile_runs if math.isfinite(getattr(run, metric)))
            if any(math.isfinite(getattr(run, metric)) for run in profile_runs) else math.nan
            for metric in METRIC_NAMES
        }
        valid = all(math.isfinite(medians[key]) for key in ("settling_time_s", "rise_time_s"))
        results.append(ProfileResult(name, profile_runs, medians, valid=valid))

    weights = {
        "settling_time_s": 0.45,
        "rise_time_s": 0.25,
        "overshoot_pct": 0.15,
        "peak_current_a": 0.10,
        "energy_j": 0.05,
    }
    valid_results = [result for result in results if result.valid]
    ranges: dict[str, tuple[float, float]] = {}
    for metric in weights:
        values = [result.medians[metric] for result in valid_results]
        ranges[metric] = (min(values), max(values)) if values else (0.0, 0.0)
    for result in results:
        if not result.valid:
            result.score = math.inf
            continue
        score = 0.0
        for metric, weight in weights.items():
            low, high = ranges[metric]
            normalised = 0.0 if high - low < 1e-12 else (result.medians[metric] - low) / (high - low)
            score += 100.0 * weight * normalised
        result.score = score
    results.sort(key=lambda result: (not result.valid, result.score, result.profile_name.lower()))
    for rank, result in enumerate(results, start=1):
        result.rank = rank
    return results


class ControllerComparisonSequence(QObject):
    """GUI-supervised test state machine; the firmware retains all safety authority."""

    status_changed = Signal(str)
    progress_changed = Signal(int, int)
    run_finished = Signal(object)
    finished = Signal(object)
    aborted = Signal(str)

    def __init__(self, send: Callable[[str], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.send = send
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._tick)
        self.running = False
        self.latest: Telemetry | None = None
        self.state = "IDLE"
        self.state_since = 0.0
        self.within_since = 0.0
        self.profiles: list[ControllerProfile] = []
        self.settings = ComparisonSettings()
        self.original_values: dict[str, float] = {}
        self.profile_index = 0
        self.repetition = 0
        self.step_time = 0.0
        self.samples: list[Telemetry] = []
        self.runs: list[RunMetrics] = []

    def start(
        self,
        profiles: list[ControllerProfile],
        settings: ComparisonSettings,
        latest: Telemetry | None,
        original_values: dict[str, float],
    ) -> None:
        if self.running:
            raise RuntimeError("A controller comparison is already running")
        if latest is None:
            raise RuntimeError("No telemetry received")
        if latest.fault_code:
            raise RuntimeError(f"Active fault: {latest.fault_text}")
        if not profiles:
            raise RuntimeError("Enable at least one controller profile")
        if abs(settings.end_pct - settings.start_pct) < 1.0:
            raise RuntimeError("Start and end position must differ by at least 1%")
        self.profiles = profiles
        self.settings = settings
        self.latest = latest
        self.original_values = {name: original_values[name] for name in PROFILE_PARAMETERS if name in original_values}
        self.profile_index = 0
        self.repetition = 0
        self.runs = []
        self.running = True
        self._apply_profile()
        self.timer.start()

    def on_telemetry(self, telemetry: Telemetry) -> None:
        self.latest = telemetry
        if self.running and self.state == "STEP_HOLD" and telemetry.pc_time >= self.step_time:
            self.samples.append(telemetry)

    def abort(self, reason: str = "Operator abort") -> None:
        if not self.running:
            return
        self.running = False
        self.timer.stop()
        self.send("CMD,STOP")
        self._restore_original()
        self.status_changed.emit("ABORTED")
        self.aborted.emit(reason)

    def _enter(self, state: str) -> None:
        self.state = state
        self.state_since = time.monotonic()
        self.within_since = 0.0
        profile = self.profiles[self.profile_index]
        self.status_changed.emit(
            f"{state} | {profile.name} | herhaling {self.repetition + 1}/{self.settings.repetitions}"
        )

    def _apply_profile(self) -> None:
        profile = self.profiles[self.profile_index]
        self.send("CMD,STOP")
        for name in PROFILE_PARAMETERS:
            if name in profile.values:
                self.send(f"SET,{name},{profile.values[name]:g}")
        self.send("CMD,PEAK_RESET")
        self._enter("PROFILE APPLY")

    def _tick(self) -> None:
        if not self.running or self.latest is None:
            return
        now = time.monotonic()
        telemetry = self.latest
        settings = self.settings
        if time.time() - telemetry.pc_time > 1.0:
            self.abort("Telemetry/communication lost")
            return
        if telemetry.fault_code:
            self.abort(f"Firmware fault: {telemetry.fault_text}")
            return
        if telemetry.current_a > settings.max_current_a:
            self.abort(f"Current {telemetry.current_a:.2f} A exceeded test limit")
            return
        if now - self.state_since > settings.move_timeout_s and self.state in {"MOVE START", "RETURN START"}:
            self.abort(f"Position was not reached within {settings.move_timeout_s:g} s")
            return

        if self.state == "PROFILE APPLY" and now - self.state_since >= 0.30:
            # Starting this test is an explicit enable action. Firmware still
            # rejects it when an E-stop, invalid sensor, or latched fault exists.
            self.send("CMD,ENABLE")
            self.send("CMD,MODE,STEP")
            self.send(f"CMD,TARGET,{settings.start_pct:.3f}")
            self._enter("MOVE START")
        elif self.state in {"MOVE START", "RETURN START"}:
            if abs(telemetry.feedback_pct - settings.start_pct) <= settings.tolerance_pct:
                if not self.within_since:
                    self.within_since = now
                elif now - self.within_since >= settings.stable_before_step_s:
                    self._enter("BASELINE")
            else:
                self.within_since = 0.0
        elif self.state == "BASELINE" and now - self.state_since >= settings.baseline_s:
            self.samples = []
            self.step_time = time.time()
            self.send(f"CMD,TARGET,{settings.end_pct:.3f}")
            self._enter("STEP_HOLD")
        elif self.state == "STEP_HOLD" and now - self.state_since >= settings.hold_s:
            self._complete_run()

    def _complete_run(self) -> None:
        profile = self.profiles[self.profile_index]
        try:
            metrics = calculate_step_metrics(
                self.samples,
                profile.name,
                self.repetition + 1,
                self.settings.start_pct,
                self.settings.end_pct,
                self.step_time,
                self.settings.tolerance_pct,
            )
        except ValueError as exc:
            self.abort(str(exc))
            return
        self.runs.append(metrics)
        self.run_finished.emit(metrics)
        completed = self.profile_index * self.settings.repetitions + self.repetition + 1
        total = len(self.profiles) * self.settings.repetitions
        self.progress_changed.emit(completed, total)
        self.repetition += 1
        if self.repetition < self.settings.repetitions:
            self.send(f"CMD,TARGET,{self.settings.start_pct:.3f}")
            self._enter("RETURN START")
            return
        self.profile_index += 1
        self.repetition = 0
        if self.profile_index < len(self.profiles):
            self._apply_profile()
            return
        self.running = False
        self.timer.stop()
        self.send("CMD,STOP")
        self._restore_original()
        results = aggregate_and_rank(self.runs)
        self.status_changed.emit("COMPLETE")
        self.finished.emit(results)

    def _restore_original(self) -> None:
        for name in PROFILE_PARAMETERS:
            if name in self.original_values:
                self.send(f"SET,{name},{self.original_values[name]:g}")
        self.send("GET,CONFIG")

