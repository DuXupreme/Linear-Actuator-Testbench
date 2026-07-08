import json

from gui.config_model import ControllerConfig


def test_defaults_are_valid() -> None:
    config = ControllerConfig.defaults()
    config.validate(config.values)
    assert config.values["STALL_ENABLE"] == 0


def test_rejects_bad_calibration_span() -> None:
    config = ControllerConfig.defaults()
    try:
        config.merge({"FB_MIN": 500, "FB_MAX": 520})
    except ValueError as exc:
        assert "span" in str(exc)
        return
    raise AssertionError("short calibration span should fail")


def test_rejects_current_limit_order() -> None:
    config = ControllerConfig.defaults()
    try:
        config.merge({"SOFT_CURRENT": 9.5, "HARD_CURRENT": 9.0})
    except ValueError as exc:
        assert "Hard current" in str(exc)
        return
    raise AssertionError("hard limit <= soft limit should fail")


def test_json_round_trip(tmp_path) -> None:
    config = ControllerConfig.defaults()
    path = tmp_path / "config.json"
    config.export_json(path)
    loaded = ControllerConfig.defaults()
    loaded.import_json(path)
    assert loaded.values == config.values

