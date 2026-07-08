"""Typed data exchanged by the serial, simulation, GUI, graph, and log layers."""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import math


class FaultCode(IntEnum):
    NONE = 0
    EMERGENCY_STOP = 1
    WATCHDOG_TIMEOUT = 2
    FEEDBACK_POT_INVALID = 3
    COMMAND_POT_INVALID = 4
    INA_NOT_DETECTED = 5
    CURRENT_INVALID = 6
    HARD_OVERCURRENT = 7
    STALL_DETECTED = 8
    LOWER_LIMIT = 9
    UPPER_LIMIT = 10
    INVALID_COMMAND = 11
    INTERNAL_CONFIG = 12


FAULT_TEXT = {
    FaultCode.NONE: "No fault",
    FaultCode.EMERGENCY_STOP: "Emergency stop active",
    FaultCode.WATCHDOG_TIMEOUT: "Serial watchdog timeout",
    FaultCode.FEEDBACK_POT_INVALID: "Feedback potentiometer invalid",
    FaultCode.COMMAND_POT_INVALID: "Command potentiometer invalid",
    FaultCode.INA_NOT_DETECTED: "INA228 not detected",
    FaultCode.CURRENT_INVALID: "Current measurement invalid",
    FaultCode.HARD_OVERCURRENT: "Hard overcurrent",
    FaultCode.STALL_DETECTED: "Stall detected",
    FaultCode.LOWER_LIMIT: "Lower software limit",
    FaultCode.UPPER_LIMIT: "Upper software limit",
    FaultCode.INVALID_COMMAND: "Invalid command",
    FaultCode.INTERNAL_CONFIG: "Internal configuration error",
}


@dataclass(slots=True)
class Telemetry:
    time_ms: int
    mode: str
    target_pct: float
    command_raw: int
    command_pct: float
    feedback_raw: int
    feedback_pct: float
    error_pct: float
    pwm: int
    current_a: float
    filtered_current_a: float
    peak_current_a: float
    bus_voltage_v: float
    shunt_voltage_mv: float
    power_w: float
    fault_code: int
    fault_latched: bool
    fault_age_ms: int
    estop: bool
    lower_limit: bool
    upper_limit: bool
    ina_ok: bool
    soft_limit_active: bool
    stall_active: bool
    control_period_us: int
    telemetry_period_us: int
    free_ram: int
    pc_time: float = 0.0

    @property
    def fault_text(self) -> str:
        try:
            return FAULT_TEXT[FaultCode(self.fault_code)]
        except (ValueError, KeyError):
            return f"Unknown fault {self.fault_code}"


@dataclass(slots=True)
class FaultState:
    code: int = 0
    text: str = "No fault"
    latched: bool = False
    age_ms: int = 0


@dataclass(slots=True)
class ConnectionState:
    connected: bool = False
    simulation: bool = False
    port: str = ""
    baud: int = 115200
    firmware_version: str = "—"
    last_telemetry_monotonic: float = 0.0
    packet_rate_hz: float = 0.0
    invalid_packets: int = 0

