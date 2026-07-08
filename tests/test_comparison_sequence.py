import math

import pytest
from PySide6.QtCore import QCoreApplication

from gui.comparison_sequence import (
    ComparisonSettings,
    ControllerComparisonSequence,
    ControllerProfile,
    RunMetrics,
    aggregate_and_rank,
    calculate_step_metrics,
)
from gui.config_model import ControllerConfig
from gui.telemetry import Telemetry


def telemetry(pc_time: float, position: float, *, current: float = 2.0,
              power: float = 24.0, fault: int = 0) -> Telemetry:
    return Telemetry(
        time_ms=round(pc_time * 1000), mode="STEP", target_pct=100.0,
        command_raw=0, command_pct=0.0, feedback_raw=0,
        feedback_pct=position, error_pct=100.0 - position, pwm=100,
        current_a=current, filtered_current_a=current, peak_current_a=current,
        bus_voltage_v=12.0, shunt_voltage_mv=0.0, power_w=power,
        fault_code=fault, fault_latched=bool(fault), fault_age_ms=0,
        estop=False, lower_limit=False, upper_limit=False, ina_ok=True,
        soft_limit_active=False, stall_active=False, control_period_us=2000,
        telemetry_period_us=20000, free_ram=1200, pc_time=pc_time,
    )


def run(name: str, rise: float, settling: float, overshoot: float,
        peak: float, energy: float) -> RunMetrics:
    return RunMetrics(
        profile_name=name, repetition=1, rise_time_s=rise,
        settling_time_s=settling, overshoot_pct=overshoot,
        movement_delay_s=0.1, steady_state_error_pct=0.2,
        peak_current_a=peak, rms_current_a=peak * 0.7, energy_j=energy,
        max_speed_pct_s=100.0, max_acceleration_pct_s2=500.0, samples=100,
    )


def test_step_metrics_use_external_position_and_integrate_energy() -> None:
    start_time = 10.0
    samples = []
    for index in range(21):
        elapsed = index * 0.1
        samples.append(telemetry(start_time + elapsed, min(100.0, elapsed * 100.0)))
    metrics = calculate_step_metrics(samples, "baseline", 1, 0.0, 100.0, start_time, 1.0)
    assert metrics.rise_time_s == pytest.approx(0.8)
    assert metrics.settling_time_s == pytest.approx(1.0)
    assert metrics.overshoot_pct == 0.0
    assert metrics.energy_j == pytest.approx(48.0)
    assert metrics.max_speed_pct_s == pytest.approx(100.0)
    assert metrics.samples == 21


def test_ranking_uses_medians_and_puts_failed_profile_last() -> None:
    results = aggregate_and_rank([
        run("fast", 0.6, 1.0, 1.0, 3.0, 20.0),
        run("fast", 0.8, 1.2, 1.2, 3.2, 22.0),
        run("slow", 1.5, 2.5, 5.0, 5.0, 40.0),
        run("failed", 0.5, math.nan, 0.0, 2.0, 10.0),
    ])
    assert [result.profile_name for result in results] == ["fast", "slow", "failed"]
    assert results[0].medians["rise_time_s"] == pytest.approx(0.7)
    assert results[0].score < results[1].score
    assert not results[-1].valid
    assert math.isinf(results[-1].score)


def test_abort_stops_motor_and_restores_original_profile() -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    sent: list[str] = []
    config = ControllerConfig.defaults().values
    profile = ControllerProfile("test", {name: config[name] for name in (
        "KP", "KI", "KD", "DEADBAND", "MIN_PWM", "MAX_PWM", "PWM_SLEW",
        "DERIV_FILTER", "FEEDBACK_FILTER",
    )})
    sequence = ControllerComparisonSequence(sent.append)
    sequence.start([profile], ComparisonSettings(), telemetry(100.0, 25.0), config)
    sequence.abort("test abort")
    assert sent[0] == "CMD,STOP"
    assert sent[-2].startswith("SET,FEEDBACK_FILTER,")
    assert sent[-1] == "GET,CONFIG"
    assert sent.count("CMD,STOP") >= 2
    assert not sequence.running


def test_start_rejects_faulted_telemetry() -> None:
    sequence = ControllerComparisonSequence(lambda _: None)
    with pytest.raises(RuntimeError, match="Active fault"):
        sequence.start([], ComparisonSettings(), telemetry(1.0, 25.0, fault=7), {})

