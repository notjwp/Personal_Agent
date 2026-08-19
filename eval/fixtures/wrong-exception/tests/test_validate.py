import pytest

from settings.validate import InvalidSetting, validate_name, validate_port


@pytest.mark.parametrize("port", [1, 80, 65535])
def test_valid_ports_pass_through(port):
    assert validate_port(port) == port


@pytest.mark.parametrize("port", [0, -1, 70000])
def test_out_of_range_ports_are_rejected(port):
    with pytest.raises(InvalidSetting):
        validate_port(port)


def test_names_must_not_be_blank_or_padded():
    assert validate_name("timeout") == "timeout"
    with pytest.raises(InvalidSetting):
        validate_name(" timeout")
