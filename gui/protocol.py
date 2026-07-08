"""Strict, testable parser/formatter for protocol version 1."""
from __future__ import annotations

import math
import time
from typing import Any

from .telemetry import Telemetry


TELEMETRY_FIELD_COUNT = 28


class ProtocolError(ValueError):
    """A serial line is well delimited but violates the protocol."""


def _float(value: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ProtocolError(f"invalid floating-point value {value!r}") from exc


def _int(value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ProtocolError(f"invalid integer value {value!r}") from exc


def _bool(value: str) -> bool:
    number = _int(value)
    if number not in (0, 1):
        raise ProtocolError(f"boolean must be 0 or 1, got {value!r}")
    return bool(number)


def parse_telemetry(line: str, pc_time: float | None = None) -> Telemetry:
    fields = line.strip().split(",")
    if len(fields) != TELEMETRY_FIELD_COUNT or fields[0] != "TEL":
        raise ProtocolError(
            f"expected TEL with {TELEMETRY_FIELD_COUNT} fields, got {len(fields)}"
        )
    return Telemetry(
        time_ms=_int(fields[1]), mode=fields[2], target_pct=_float(fields[3]),
        command_raw=_int(fields[4]), command_pct=_float(fields[5]),
        feedback_raw=_int(fields[6]), feedback_pct=_float(fields[7]),
        error_pct=_float(fields[8]), pwm=_int(fields[9]),
        current_a=_float(fields[10]), filtered_current_a=_float(fields[11]),
        peak_current_a=_float(fields[12]), bus_voltage_v=_float(fields[13]),
        shunt_voltage_mv=_float(fields[14]), power_w=_float(fields[15]),
        fault_code=_int(fields[16]), fault_latched=_bool(fields[17]),
        fault_age_ms=_int(fields[18]), estop=_bool(fields[19]),
        lower_limit=_bool(fields[20]), upper_limit=_bool(fields[21]),
        ina_ok=_bool(fields[22]), soft_limit_active=_bool(fields[23]),
        stall_active=_bool(fields[24]), control_period_us=_int(fields[25]),
        telemetry_period_us=_int(fields[26]), free_ram=_int(fields[27]),
        pc_time=time.time() if pc_time is None else pc_time,
    )


def parse_line(line: str) -> tuple[str, Any]:
    clean = line.strip()
    if not clean:
        raise ProtocolError("empty line")
    prefix = clean.split(",", 1)[0]
    if prefix == "TEL":
        return prefix, parse_telemetry(clean)
    if prefix == "CFG":
        fields = clean.split(",", 2)
        if len(fields) == 2 and fields[1] == "END":
            return prefix, ("END", None)
        if len(fields) != 3:
            raise ProtocolError("CFG requires name and value")
        try:
            value: Any = float(fields[2])
        except ValueError:
            value = fields[2]
        return prefix, (fields[1], value)
    if prefix in {"ACK", "ERR", "VER", "STATUS", "EVT"}:
        return prefix, clean.split(",")[1:]
    raise ProtocolError(f"unknown packet prefix {prefix!r}")


def command(*parts: object) -> str:
    values = [str(part).strip() for part in parts]
    if not values or any(not value or "," in value or "\n" in value for value in values):
        raise ValueError("command parts must be non-empty and contain no comma/newline")
    return ",".join(values)
