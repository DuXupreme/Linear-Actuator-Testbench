# Actuator Testbench serial protocol

Version 1.0. Transport is 115200 baud, 8 data bits, no parity, one stop bit.
Packets are printable ASCII terminated by `\n`; optional `\r` is ignored. The
maximum received line is 79 characters. Names are case-insensitive; values use
a dot decimal separator. The Arduino sends one response for every command that
changes or requests state.

## PC to Arduino

```text
CMD,HEARTBEAT
CMD,STOP
CMD,ENABLE
CMD,RESET_FAULT
CMD,MODE,DISABLED|MANUAL|PWM|POSITION|FOLLOW|STEP|CURRENT_TEST
CMD,PWM,-255..255
CMD,TARGET,percent
CMD,MANUAL,-1|0|1,pwm
CMD,CAL,FB|CMD,MIN|MAX
CMD,PEAK_RESET
SET,name,value
GET,CONFIG
GET,VERSION
GET,STATUS
SAVE,CONFIG
LOAD,CONFIG
DEFAULTS,CONFIG
```

`CMD,STOP` always disables the bridge and changes mode to `DISABLED`.
`CMD,ENABLE` only arms control; it never starts motion. Mode changes also force
zero output and reset the PID state before the new mode becomes active.
`CMD,HEARTBEAT` must be sent periodically (GUI default 250 ms). Motion stops if
no valid command is received within `WATCHDOG_MS` (default 750 ms).

Parameter names are listed by `GET,CONFIG` and in `configs/default_config.json`.
Boolean values are `0` or `1`. Every accepted line refreshes the watchdog.

## Arduino responses

```text
ACK,original-command-summary
ERR,code,readable text
VER,ACTUATOR_TESTBENCH,1.0.0,PROTOCOL,1
CFG,name,value
CFG,END
STATUS,enabled,mode,fault_code,fault_latched
EVT,FAULT,fault_code,latched,readable text
```

Configuration reads produce several `CFG` lines followed by `CFG,END` and an
`ACK,GET,CONFIG`. Version and status requests return `VER` and `STATUS`
respectively; they do not need a redundant ACK. Unknown/malformed commands
produce `ERR,INVALID_COMMAND,...`.

## Telemetry

Telemetry is fixed-order CSV, normally sent at 50 Hz:

```text
TEL,time_ms,mode,target_pct,command_pot_raw,command_pot_pct,feedback_raw,feedback_pct,error_pct,pwm,current_A,filtered_current_A,peak_current_A,bus_voltage_V,shunt_voltage_mV,power_W,fault_code,fault_latched,fault_age_ms,estop,lower_limit,upper_limit,ina_ok,soft_limit_active,stall_active,control_period_us,telemetry_period_us,free_ram
```

There are 28 fields including `TEL`. `mode` and numeric fields never contain
commas. Invalid floating measurements are sent as `nan`. Fault text is obtained
from the fault-code mapping in the GUI; asynchronous `EVT,FAULT` lines also
carry readable text.

### Mode values

`DISABLED`, `MANUAL`, `PWM`, `POSITION`, `FOLLOW`, `STEP`, `CURRENT_TEST`.

### Fault codes

| Code | Meaning |
|---:|---|
| 0 | No fault |
| 1 | Emergency stop |
| 2 | Serial watchdog timeout |
| 3 | Feedback potentiometer invalid |
| 4 | Command potentiometer invalid |
| 5 | INA228 not detected |
| 6 | Current measurement invalid |
| 7 | Hard overcurrent |
| 8 | Stall detected |
| 9 | Lower software limit |
| 10 | Upper software limit |
| 11 | Invalid command |
| 12 | Internal configuration error |

Limit faults and watchdog faults stop movement but are not latched. Emergency
stop, hard overcurrent, stall, invalid feedback, and critical sensor/configuration
faults latch and require `CMD,RESET_FAULT` after the condition is removed.
