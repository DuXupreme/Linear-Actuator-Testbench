# Actuator Testbench

Actuator Testbench is a proof-of-concept laboratory application for exploring,
calibrating, and tuning a 24–48 V brushed-DC linear actuator. A classic 5 V
Arduino Nano with ATmega328P
enforces the time-critical control and safety rules. A Windows/Python GUI gives
live visibility, tuning, graphs, CSV logging, scripted step tests, diagnostics,
and a hardware-free simulator.

**Nederlandstalige eerste keer?** Begin met
[`SNELSTART_NL.md`](SNELSTART_NL.md). Daar staat stap voor stap hoe je eerst de
simulatie en daarna veilig de echte Nano gebruikt.

The firmware starts disabled. Connecting or reconnecting never resumes motion.
Only an explicit **ENABLE CONTROL** followed by a mode/command can move the
actuator. The serial watchdog, emergency input, sensor checks, current limits,
stall detector, and software endpoints are enforced on the Arduino.

> **Laboratory test software. Keep an accessible physical emergency stop and
> current-limited power supply. Do not test unattended.** This input is a logic
> safety input, not a safety-rated power disconnect. A physical E-stop should
> also remove actuator energy through appropriately rated hardware.

## Hardware and wiring

Disconnect power while wiring. Start at a low current limit and, where possible,
a reduced bus voltage. Verify every potentiometer stays within 0–5 V at its
Arduino input over the full mechanical travel. Never apply the 24–48 V bus to an
Arduino pin.

| From | To | Notes |
|---|---|---|
| Arduino 5 V | BTS7960/HW-039 logic VCC | Confirm the exact module labeling |
| Arduino GND | H-bridge GND, pot grounds, E-stop return, INA228 logic GND | Logic common ground required |
| D9 | RPWM | Positive firmware direction by default |
| D10 | LPWM | Negative firmware direction by default |
| D7 | R_EN | Bridge enable |
| D8 | L_EN | Bridge enable |
| A0 | actuator feedback pot wiper | Pot ends to 5 V and GND |
| A1 | command pot wiper | Pot ends to 5 V and GND |
| D2 | E-stop logic input | Active low; internal pull-up is enabled |
| Bridge M+ / M- | actuator motor leads | Swap leads or use `MOTOR_INVERT` if direction is wrong |

### Default Arduino pins

All defaults are together at the top of
`firmware/actuator_testbench/config.h`; change those constants rather than
editing numbers throughout the program.

| Function | Default |
|---|---:|
| Feedback potentiometer | A0 |
| Command potentiometer | A1 |
| RPWM / LPWM | D9 / D10 |
| R_EN / L_EN | D7 / D8 |
| Active-low E-stop | D2 (`INPUT_PULLUP`) |
| INA228 SDA / SCL | A4 / A5 (Nano hardware I2C) |
| INA228 address | 0x40 |

### INA228 high-side wiring

| Connection | Wiring |
|---|---|
| Arduino 5 V / GND | INA228 logic VIN / GND |
| Arduino A4 / A5 | INA228 SDA / SCL |
| Supply positive | INA228 high-side current input (`VIN+`) |
| INA228 current output (`VIN-`) | H-bridge bus positive (`B+`) |
| Supply negative | H-bridge bus negative and common logic ground |

The Adafruit breakout is configured with `ina228.setShunt(0.015, 10.0)` for its
15 mΩ shunt and approximately 10 A range. The firmware uses I2C address 0x40.
If the address is changed by jumpers, edit `INA228_ADDRESS` in `config.h`.

Because the sensor is before the bridge, it measures **supply current**, not
necessarily instantaneous motor winding current during PWM recirculation. The
software therefore uses progressive slow limiting and a time-qualified trip; it
does not pretend to be a fast hardware current regulator.

## Software installation

### Arduino

Install Arduino IDE 2.x, the **Arduino AVR Boards** platform, and these libraries
through Library Manager:

- **Adafruit INA228** (which also installs Adafruit BusIO)
- `Wire` and `EEPROM` are included with the Arduino AVR core

The INA228 calls and units follow the current [official Adafruit INA228 library](https://github.com/adafruit/Adafruit_INA228).

Open `firmware/actuator_testbench/actuator_testbench.ino`, choose **Arduino
Nano**, then choose processor **ATmega328P**. Many Nano clones use
**ATmega328P (Old Bootloader)**; select that processor if a normal upload fails.
Select the correct port, compile, and upload. Open Serial Monitor at 115200 baud
to inspect the ASCII protocol. A boot
should print:

```text
VER,ACTUATOR_TESTBENCH,1.0.0,PROTOCOL,1
```

The motor remains disabled after upload/reset. If compilation reports that
`Adafruit_INA228.h` is missing, install the Adafruit INA228 library and restart
the IDE.

### Python GUI on Windows

Python 3.12 or newer is required. In PowerShell, from this directory:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
python run_gui.py
```

If PowerShell blocks activation, use
`Set-ExecutionPolicy -Scope Process RemoteSigned`, then activate again. The GUI
uses PySide6, pyserial, pyqtgraph, and NumPy. `pytest` is included for the local
test suite.

## First connection

1. Mechanically support the actuator and make the physical E-stop reachable.
2. Turn the power-supply current limit down. Leave the actuator bus off.
3. Connect USB, launch the GUI, refresh ports, select the Nano, and click Connect.
4. Confirm firmware version, telemetry, sensible ADC values, `INA228: connected`,
   and `NO FAULT`. Connection sends STOP and does not enable movement.
5. Calibrate both potentiometers with motor power still off.
6. Apply bus power, press **ENABLE CONTROL**, select Manual, and use low-PWM
   hold-to-run jogs. Release immediately if direction/feedback is wrong.

The GUI sends a heartbeat every 250 ms. Firmware defaults to a 750 ms watchdog.
Closing the GUI, disconnecting, losing the cable, or stopping heartbeat disables
the output. The red STOP and spacebar shortcut work from every tab.

## Potentiometer calibration

No calibration command moves the actuator automatically.

1. Remove motor power or move the mechanism safely to its physical minimum.
2. In **Sensor calibration**, confirm the raw reading is not near 0 or 1023 and
   click **Capture current as minimum** for the relevant sensor.
3. Move to the physical maximum and capture maximum.
4. The span must be at least 50 ADC counts. A much larger span is preferable.
5. Move through the range and verify normalized percentage increases. Use the
   sensor's **Invert** checkbox if it decreases.
6. Repeat independently for feedback and command pots.
7. Click **Save calibrated configuration to EEPROM**.

The firmware treats raw ADC readings at 0–2 or 1021–1023 as disconnected/shorted.
It never drives toward an invalid signal. Calibration min/max map to 0–100%; the
software travel limits then constrain the usable portion of that normalized span.

## Operating modes

Only one mode is active. Mode changes command zero and reset PID state first.

- **Disabled:** bridge output is zero and control is disarmed.
- **Manual direction:** select Manual, choose a modest PWM, and hold Extend or
  Retract. With hold-to-run enabled, release sends zero immediately. Optional jog
  duration applies when hold-to-run is off.
- **Direct PWM:** select Direct PWM and send a signed value from −255 to +255.
- **Position target:** select Position target, choose a target inside the software
  limits, and send it. Targets may be changed while running.
- **Follow potentiometer:** select Follow or press Start follow. The command pot's
  normalized position becomes the target continuously. Filters may be disabled.
- **Step response:** use the dedicated Step response tab; the GUI supervises the
  test while firmware remains responsible for motion protection.
- **Current / stall test:** select this mode and use controlled signed PWM while
  watching current. Stall detection remains off until explicitly checked.

**Direction check:** if positive movement physically retracts, first STOP. Change
`MOTOR_INVERT`, or safely swap motor leads with power removed. Feedback inversion
is independent and should be set so normalized position rises toward extension.

## Controller behavior and tuning

Position and follow modes run at 500 Hz by default. Outside the deadband the
controller computes `Kp·error + Ki·integral + Kd·error_rate`. Inside the deadband
it sets PWM to zero and resets the integral—minimum PWM is never applied there.
Outside it, minimum effective PWM compensates motor stiction. Integral clamping
and conditional integration prevent wind-up; derivative filtering reduces ADC
noise. Reversal delay and PWM slew avoid an instant full-output reversal.

The **Controller tuning** tab presents every control-loop parameter as a visual
card with a slider, exact numeric entry, allowed range, qualitative mini-graph,
and Dutch hover explanation. The combined curve at the top shows how Kp,
deadband, minimum PWM, and maximum PWM shape the static error-to-output response.

Tune with low current/PWM limits:

1. Set `Ki=0`, a small `Kd`, and moderate slew. Increase `Kp` until response is
   useful but not persistently oscillatory.
2. Increase deadband slightly if it chatters at rest. Do not use minimum PWM to
   cure chatter inside the deadband.
3. Raise minimum PWM only enough to start reliable motion.
4. Add `Kd` for damping; if readings become noisy, increase derivative filtering.
5. Add a little `Ki` only for persistent load error, keeping the integral limit
   conservative.
6. Increase slew rate for response or reduce it for gentler starts/reversals.

`SLOWDOWN_ZONE` progressively applies the separate near-endstop PWM limit. The
response presets are ordinary visible parameter changes, not hidden modes. Press
Apply all, test, then Save to EEPROM only after values are proven. Slider edits
are live but are never automatically written to EEPROM. Saving intentionally
stops and disables the actuator first because AVR EEPROM writes would otherwise
interrupt the real-time control loop.

## Current limits and stall protection

- `CURRENT_WARN` is a GUI warning/reference level.
- Above `SOFT_CURRENT`, allowed PWM is progressively reduced as current approaches
  `HARD_CURRENT`.
- Current above `HARD_CURRENT` for `HARD_CURRENT_MS` latches hard overcurrent,
  disables the bridge, and requires Reset fault after the condition is removed.
- Optional stall detection requires PWM, current, insufficient position movement,
  and time criteria simultaneously. It is **disabled by default**. Prove the
  thresholds with conservative tests before enabling it.

Use a hardware current-limited supply regardless of software settings. The
INA228's sample rate and upstream location cannot protect against every fast
electrical fault.

## Step-response tests

1. Connect, calibrate, configure conservative current limits, enable control,
   and start CSV logging if permanent raw data is wanted.
2. Set start/end positions, baseline delay, hold time, repetitions, test current
   ceiling, total timeout, return behavior, and position tolerance.
3. Press **START STEP TEST**. The sequence checks the current telemetry fault,
   moves to start, settles, records baseline, applies the step, holds, optionally
   returns, and repeats.
4. STOP, communication loss, firmware fault, test-current excess, or total timeout
   aborts the sequence and commands STOP.

The results show rise time (10–90%), settling time, overshoot, maximum current,
steady-state error, movement start delay, and raw sample count where calculable.
Raw traces remain visible; CSV logging is recommended for formal comparison.

## Controller comparison and response optimisation

Use **Controller comparison** to test several live controller profiles under the
same positional step and mechanical load. The test does not write profiles to
EEPROM and restores the original live parameters after completion or abort.

1. Add the current settings, duplicate a row, and edit the values to compare; or
   choose `KP`, `KD`, `MAX_PWM`, `PWM_SLEW`, `MIN_PWM`, or `DEADBAND` and create
   an automatic low/current/high sweep. Verify every enabled row before starting.
2. Choose start/end positions safely inside the software limits, at least three
   repetitions, a hold time long enough to settle, and a conservative current
   abort threshold.
3. Press **START CONTROLLER COMPARISON** and confirm the physical-safety prompt.
   This explicit test action enables control. STOP, stale telemetry, a firmware
   fault, excess test current, or a position timeout aborts the complete test.
4. The GUI returns to the start position between repetitions, automatically logs
   when selected, restores the original profile, and ranks the median results.
5. Export the summary CSV or load the raw `actuator_*.csv` in CANalyser.

The ranking uses a relative 0–100 score inside the current test set (lower is
better): 45% settling time, 25% rise time, 15% overshoot, 10% peak supply current,
and 5% measured electrical energy. The table also reports movement delay,
steady-state error, RMS current, maximum position speed, and maximum position
acceleration. These position metrics come from the external feedback potmeter.

This is a **response/controller comparison**, not a calibrated torque test. The
high-side INA228 measures supply current before the H-bridge; PWM recirculation
means that this is not instantaneous motor-winding current. True motor-torque
control needs a much faster motor-current measurement and inner current loop.
True linear output-force measurement needs a calibrated load cell in the load
path. Until that hardware is added, compare current and energy only as relative
effort indicators under an unchanged mechanical load.

## Graphs and logging

Graph tabs cover target/command/position, error, PWM, current, voltage, and power.
Choose a 5/10/30/60/120-second window, pause/resume, clear, auto-range, set manual
Y limits, toggle traces, and inspect cursor coordinates. Memory is capped at 6500
samples, so long runs do not grow without bound.

**START LOGGING** writes `logs/actuator_YYYY-MM-DD_HH-MM-SS.csv` on a background
thread. It contains PC and Arduino timestamps, mode, command/target/actual/error,
PWM, INA228 values, fault information, limits, and E-stop state. A same-named
JSON sidecar records GUI/firmware versions, COM port, baud, date, and the complete
configuration snapshot. When logging stops, the complete CSV is also rendered to
`actuator_YYYY-MM-DD_HH-MM-SS_graphs.png` with position, error, PWM, current,
voltage, and power graphs. After pressing STOP LOGGING (or closing while logging)
the GUI asks whether to keep the CSV/JSON and create the graph, or permanently
delete all files belonging to that run. Keeping is the default choice. The
filename is Windows-safe. STOP logging before moving files.

### Compare runs in CANalyser

The companion CANalyser desktop app can import several of these telemetry CSVs
directly. In CANalyser choose **Actuator CSV**, read the format explanation, and select two or more
`logs/actuator_*.csv` files, and open them. No DBC is required. CANalyser keeps
every original sample, shifts each run's time axis so its first STEP target
transition is `t=0`, and creates ready-made overlays for position, error, PWM,
current, voltage, and power. Negative time is the pre-step baseline.

## Simulation mode

Check **Simulation mode (no hardware)** and Connect. It generates a delayed
position response, moving command potentiometer, PWM, load-dependent current,
voltage, power, and normal protocol/configuration replies. All ordinary GUI,
graph, log, and test-sequence code is reused. The connection banner says
`SIMULATION — no hardware`. Diagnostics also has a button to inject a simulated
latched hard-overcurrent fault. Simulation is useful for UI familiarization, not
for validating real tuning values.

## Serial protocol and EEPROM

The exact field order, commands, replies, modes, and faults are documented in
[`docs/SERIAL_PROTOCOL.md`](docs/SERIAL_PROTOCOL.md). It is line-oriented ASCII
at 115200 baud and easy to inspect with Serial Monitor. Every mutating command
receives `ACK` or `ERR`; malformed lines cannot enable motion.

EEPROM stores a configuration magic value, format version, data, and checksum.
Invalid or obsolete data is rejected and safe compiled defaults remain active.
Writes happen only on **Save to EEPROM**, and `EEPROM.update` avoids rewriting
unchanged bytes. Load, defaults, and configuration reads are explicit actions.

## Project layout

```text
actuator_testbench/
├── firmware/actuator_testbench/  Arduino sketch and centralized pin/config header
├── gui/                          Qt window, workers, parser, graphs, logger, tests
├── configs/default_config.json   PC-side safe defaults
├── docs/SERIAL_PROTOCOL.md       exact ASCII protocol
├── logs/                         generated CSV/JSON test records
├── tests/                        parser and configuration tests
├── requirements.txt
└── run_gui.py
```

Run tests with `python -m pytest`. A hardware compile/upload and low-energy bench
test are still required because automated Python tests cannot verify wiring,
actuator polarity, H-bridge clone behavior, or physical limit placement.

## Troubleshooting

| Symptom | Checks |
|---|---|
| No COM port | Refresh, use a data-capable USB cable, install the board's USB driver, close Serial Monitor |
| Connects but no telemetry | Match 115200 baud, reset Nano, confirm firmware boot `VER`, inspect Diagnostics |
| Upload to Nano fails | Try Tools → Processor → ATmega328P (Old Bootloader); for CH340 clones install the correct USB driver |
| Enable rejected | Release E-stop, validate feedback ADC, connect INA228 or deliberately disable `REQUIRE_INA`, then Reset fault |
| INA228 not detected | Check 5 V/GND/SDA/SCL, address 0x40, common ground, and Adafruit library/wiring |
| Current looks 1000× wrong | Use this firmware's mA→A and mW→W conversion; do not mix library API unit assumptions |
| Pot fault at endpoint | Wiper may be hitting ADC rail; inspect wiring and arrange a small electrical margin from 0/1023 |
| Runs opposite direction | STOP; use motor inversion or swap motor leads with power off |
| Position runs away | STOP immediately; feedback direction is wrong or mechanically uncoupled |
| Chatters near target | Increase deadband/filtering, lower Kp/min PWM, add modest derivative damping |
| Will not start moving | Verify enable/mode/fault, raise minimum PWM gradually, check soft/current limits |
| Trips software limit early | Recalibrate raw min/max, then set normalized lower/upper limits |
| Watchdog fault | Check USB/COM stability and that only one application owns the port |
| GUI is slow | Pause raw console, shorten visible graph work, and confirm telemetry is near 50 Hz |
| EEPROM load rejected | Restore defaults, recalibrate, and explicitly Save; firmware/config versions may have changed |

## Important implementation assumptions

- Positive signed PWM drives RPWM; the actual extend direction depends on motor
  wiring and `MOTOR_INVERT`.
- Coast stop (both PWM zero and both enable pins low) is the safe default.
  Optional brake stop leaves enables high with both PWM pins low; braking behavior
  varies across BTS7960 clone boards, so test it at low energy before use.
- Software endpoints are secondary protection and do not replace physical travel
  limits, mechanical stops, or a power-removing E-stop.
- The GUI-supervised step test is appropriate for a proof of concept; firmware
  safety remains authoritative if the PC stalls or disconnects.
