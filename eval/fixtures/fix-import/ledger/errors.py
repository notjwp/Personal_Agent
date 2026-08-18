"""Exception types raised across the ledger package."""


class LedgerError(Exception):
    """Base class for every error this package raises."""


class ParseError(LedgerError):
    """Raised when a ledger line cannot be turned into a Transaction."""

    def __init__(self, line_no: int, text: str, reason: str) -> None:
        super().__init__(f"line {line_no}: {reason}: {text!r}")
        self.line_no = line_no
        self.text = text
        self.reason = reason


class ValidationError(LedgerError):
    """Raised when a field fails a validation rule."""

    def __init__(self, field: str, value: object, reason: str) -> None:
        super().__init__(f"{field}={value!r}: {reason}")
        self.field = field
        self.value = value
        self.reason = reason
