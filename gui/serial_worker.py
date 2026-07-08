"""Non-blocking serial and simulation data sources.

Both workers expose the same Qt signals, so the rest of the GUI is deliberately
unaware of whether telemetry came from hardware or the simulator.
"""
from __future__ import annotations

import math
from queue import Empty, Queue
import random
import threading
import time
from typing import Any

from PySide6.QtCore import QThread, Signal
import serial

from .config_model import ControllerConfig
from .protocol import ProtocolError, parse_line
from .telemetry import Telemetry


class DataWorker(QThread):
    telemetry_received = Signal(object)
    line_received = Signal(str)
    line_sent = Signal(str)
    packet_received = Signal(str, object)
    error = Signal(str)
    connection_changed = Signal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self._outgoing: Queue[str] = Queue()
        self._stop_event = threading.Event()

    def send(self, line: str) -> None:
        clean = line.strip()
        if clean:
            self._outgoing.put(clean)

    def stop(self) -> None:
        self._stop_event.set()

    def _emit_parsed(self, line: str) -> None:
        self.line_received.emit(line)
        try:
            kind, payload = parse_line(line)
        except ProtocolError as exc:
            self.error.emit(f"Parse error: {exc}; line={line!r}")
            return
        if kind == "TEL":
            self.telemetry_received.emit(payload)
        self.packet_received.emit(kind, payload)


class SerialWorker(DataWorker):
    def __init__(self, port: str, baud: int = 115200) -> None:
        super().__init__()
        self.port = port
        self.baud = baud

    def run(self) -> None:
        link: serial.Serial | None = None
        try:
            link = serial.Serial(self.port, self.baud, timeout=0.02, write_timeout=0.1)
            link.reset_input_buffer()
            self.connection_changed.emit(True, f"Connected to {self.port}")
            for initial in ("CMD,STOP", "GET,VERSION", "GET,CONFIG"):
                self._write(link, initial)
            next_heartbeat = time.monotonic()
            while not self._stop_event.is_set():
                now = time.monotonic()
                if now >= next_heartbeat:
                    self._write(link, "CMD,HEARTBEAT")
                    next_heartbeat = now + 0.25
                self._drain_outgoing(link)
                try:
                    raw = link.readline()
                except serial.SerialException as exc:
                    raise ConnectionError(f"Serial read failed: {exc}") from exc
                if raw:
                    self._emit_parsed(raw.decode("ascii", errors="replace").strip())
        except (serial.SerialException, ConnectionError, OSError) as exc:
            self.error.emit(str(exc))
        finally:
            if link is not None and link.is_open:
                try:
                    link.write(b"CMD,STOP\n")
                    link.flush()
                except (serial.SerialException, OSError) as exc:
                    self.error.emit(f"Could not send final STOP during disconnect: {exc}")
                link.close()
            self.connection_changed.emit(False, "Disconnected")

    def _write(self, link: serial.Serial, line: str) -> None:
        try:
            link.write((line + "\n").encode("ascii"))
            self.line_sent.emit(line)
        except (UnicodeEncodeError, serial.SerialException, serial.SerialTimeoutException) as exc:
            raise ConnectionError(f"Serial write failed: {exc}") from exc

    def _drain_outgoing(self, link: serial.Serial) -> None:
        for _ in range(30):
            try:
                line = self._outgoing.get_nowait()
            except Empty:
                return
            self._write(link, line)


class SimulationWorker(DataWorker):
    """Simple delayed actuator model with the exact real telemetry interface."""

    def __init__(self) -> None:
        super().__init__()
        self.cfg = ControllerConfig.defaults()
        self.enabled = False
        self.mode = "DISABLED"
        self.position = 35.0
        self.target = 35.0
        self.command_pot = 50.0
        self.pwm = 0.0
        self.peak_current = 0.0
        self.started = time.monotonic()
        self.fault_code = 0
        self.fault_latched = False
        self.fault_since = 0.0

    def run(self) -> None:
        self.connection_changed.emit(True, "SIMULATION — no hardware")
        self._emit_parsed("VER,ACTUATOR_TESTBENCH_SIM,1.0.0,PROTOCOL,1")
        self._send_config()
        last = time.monotonic()
        next_tel = last
        try:
            while not self._stop_event.is_set():
                now = time.monotonic()
                dt = min(0.05, now - last)
                last = now
                self._process_commands()
                self._advance(dt, now)
                if now >= next_tel:
                    telemetry = self._telemetry(now)
                    self.telemetry_received.emit(telemetry)
                    self.line_received.emit(self._telemetry_line(telemetry))
                    self.packet_received.emit("TEL", telemetry)
                    next_tel = now + 0.02
                self.msleep(2)
        finally:
            self.enabled = False
            self.mode = "DISABLED"
            self.connection_changed.emit(False, "Simulation stopped")

    def _process_commands(self) -> None:
        for _ in range(40):
            try:
                line = self._outgoing.get_nowait()
            except Empty:
                return
            self.line_sent.emit(line)
            fields = line.split(",")
            try:
                if fields[:2] == ["CMD", "STOP"]:
                    self.enabled = False; self.mode = "DISABLED"; self.pwm = 0
                elif fields[:2] == ["CMD", "ENABLE"]:
                    if not self.fault_latched: self.enabled = True
                elif fields[:2] == ["CMD", "RESET_FAULT"]:
                    self.fault_code = 0; self.fault_latched = False
                elif fields[:2] == ["CMD", "MODE"]:
                    self.pwm = 0; self.mode = fields[2]
                    if self.mode == "DISABLED": self.enabled = False
                elif fields[:2] == ["CMD", "TARGET"]:
                    self.target = max(self.cfg.values["LOWER_LIMIT"], min(self.cfg.values["UPPER_LIMIT"], float(fields[2])))
                elif fields[:2] == ["CMD", "PWM"]:
                    self.pwm = max(-255, min(255, float(fields[2])))
                elif fields[:2] == ["CMD", "MANUAL"]:
                    self.pwm = int(fields[2]) * float(fields[3])
                elif fields[0] == "SET":
                    self.cfg.update(fields[1], float(fields[2]))
                elif fields[:2] == ["GET", "CONFIG"]:
                    self._send_config()
                elif fields[:2] == ["GET", "VERSION"]:
                    self._emit_parsed("VER,ACTUATOR_TESTBENCH_SIM,1.0.0,PROTOCOL,1")
                elif fields[:2] == ["DEFAULTS", "CONFIG"]:
                    self.cfg = ControllerConfig.defaults()
                elif fields[:2] == ["CMD", "PEAK_RESET"]:
                    self.peak_current = 0
                elif fields[:2] == ["SIM", "FAULT"]:
                    self.fault_code = int(fields[2]); self.fault_latched = self.fault_code != 0; self.fault_since = time.monotonic()
                self._emit_parsed("ACK," + ",".join(fields[:2]))
            except (ValueError, IndexError, KeyError) as exc:
                self._emit_parsed(f"ERR,INVALID_COMMAND,{exc}")

    def _send_config(self) -> None:
        for name, value in self.cfg.values.items():
            self._emit_parsed(f"CFG,{name},{value:g}")
        self._emit_parsed("CFG,END")

    def _advance(self, dt: float, now: float) -> None:
        self.command_pot = 50 + 35 * math.sin((now - self.started) * 0.20)
        if not self.enabled or self.fault_latched or self.mode == "DISABLED":
            demanded = 0.0
        elif self.mode == "FOLLOW":
            self.target = self.command_pot
            demanded = (self.target - self.position) * self.cfg.values["KP"]
        elif self.mode in {"POSITION", "STEP"}:
            error = self.target - self.position
            demanded = 0 if abs(error) <= self.cfg.values["DEADBAND"] else error * self.cfg.values["KP"]
        else:
            demanded = self.pwm
        demanded = max(-self.cfg.values["MAX_PWM"], min(self.cfg.values["MAX_PWM"], demanded))
        if demanded and abs(demanded) < self.cfg.values["MIN_PWM"]:
            demanded = math.copysign(self.cfg.values["MIN_PWM"], demanded)
        slew = self.cfg.values["PWM_SLEW"] * dt
        self.pwm += max(-slew, min(slew, demanded - self.pwm)) if slew else demanded - self.pwm
        velocity = 28.0 * self.pwm / 255.0
        self.position = max(self.cfg.values["LOWER_LIMIT"], min(self.cfg.values["UPPER_LIMIT"], self.position + velocity * dt))

    def _telemetry(self, now: float) -> Telemetry:
        load = 0.35 + 3.8 * abs(self.pwm) / 255.0
        current = max(0.0, load + random.uniform(-0.08, 0.08)) if self.enabled else 0.03
        self.peak_current = max(self.peak_current, current)
        age = int((now - self.fault_since) * 1000) if self.fault_code else 0
        return Telemetry(
            int((now-self.started)*1000), self.mode, self.target,
            int(80+self.command_pot*8.6), self.command_pot,
            int(80+self.position*8.6), self.position, self.target-self.position,
            int(self.pwm), current, current*.95, self.peak_current, 47.8,
            current*15.0, current*47.8, self.fault_code, self.fault_latched,
            age, False, self.position <= self.cfg.values["LOWER_LIMIT"]+.01,
            self.position >= self.cfg.values["UPPER_LIMIT"]-.01, True,
            current > self.cfg.values["SOFT_CURRENT"], False, 2000, 20000, 1420,
            pc_time=time.time(),
        )

    @staticmethod
    def _telemetry_line(t: Telemetry) -> str:
        return (f"TEL,{t.time_ms},{t.mode},{t.target_pct:.2f},{t.command_raw},{t.command_pct:.2f},"
                f"{t.feedback_raw},{t.feedback_pct:.2f},{t.error_pct:.2f},{t.pwm},"
                f"{t.current_a:.3f},{t.filtered_current_a:.3f},{t.peak_current_a:.3f},"
                f"{t.bus_voltage_v:.2f},{t.shunt_voltage_mv:.3f},{t.power_w:.2f},"
                f"{t.fault_code},{int(t.fault_latched)},{t.fault_age_ms},{int(t.estop)},"
                f"{int(t.lower_limit)},{int(t.upper_limit)},{int(t.ina_ok)},"
                f"{int(t.soft_limit_active)},{int(t.stall_active)},{t.control_period_us},"
                f"{t.telemetry_period_us},{t.free_ram}")


def available_ports() -> list[tuple[str, str]]:
    from serial.tools import list_ports
    return [(p.device, p.description) for p in list_ports.comports()]
