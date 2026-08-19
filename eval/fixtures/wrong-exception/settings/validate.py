"""Value rules applied before storing."""


class InvalidSetting(ValueError):
    """Raised when a value fails a rule."""


def validate_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise InvalidSetting(f"port out of range: {port}")
    return port


def validate_name(name: str) -> str:
    if not name or name.strip() != name:
        raise InvalidSetting(f"bad setting name: {name!r}")
    return name
