import math

from gui.protocol import ProtocolError, parse_telemetry


LINE = (
    "TEL,1234,FOLLOW,55.00,510,50.00,520,51.20,3.80,80,1.234,1.100,"
    "2.500,47.80,18.510,58.98,0,0,0,0,0,0,1,0,0,2000,20000,1420"
)


def test_parse_complete_telemetry() -> None:
    t = parse_telemetry(LINE, pc_time=100.0)
    assert t.mode == "FOLLOW"
    assert t.feedback_raw == 520
    assert t.feedback_pct == 51.2
    assert t.current_a == 1.234
    assert t.ina_ok is True
    assert t.free_ram == 1420
    assert t.pc_time == 100.0


def test_parse_nan_measurement() -> None:
    line = LINE.replace("1.234", "nan", 1)
    assert math.isnan(parse_telemetry(line).current_a)


def test_wrong_field_count_rejected() -> None:
    try:
        parse_telemetry("TEL,1,DISABLED")
    except ProtocolError:
        return
    raise AssertionError("short telemetry should fail")

