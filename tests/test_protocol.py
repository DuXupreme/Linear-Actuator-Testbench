from gui.protocol import ProtocolError, command, parse_line


def test_command_formatter() -> None:
    assert command("CMD", "TARGET", 55.0) == "CMD,TARGET,55.0"


def test_command_formatter_rejects_delimiters() -> None:
    try:
        command("CMD", "bad,value")
    except ValueError:
        return
    raise AssertionError("comma should be rejected")


def test_parse_config_and_ack() -> None:
    assert parse_line("CFG,KP,3.5") == ("CFG", ("KP", 3.5))
    assert parse_line("CFG,END") == ("CFG", ("END", None))
    assert parse_line("ACK,SET,KP") == ("ACK", ["SET", "KP"])


def test_unknown_prefix_rejected() -> None:
    try:
        parse_line("BOGUS,1")
    except ProtocolError:
        return
    raise AssertionError("unknown prefix should fail")

