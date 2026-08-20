"""The shared normaliser.

Each field type normalises differently, so `kind` is required - there is no
sensible default, and guessing would silently corrupt data.
"""

KINDS = ("email", "phone", "postcode", "name", "url", "slug")


def normalise(value: str, kind: str) -> str:
    """Normalise `value` according to `kind`."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind}")
    text = value.strip()
    if kind == "email":
        return text.lower()
    if kind == "phone":
        return "".join(c for c in text if c.isdigit())
    if kind == "postcode":
        return text.upper().replace(" ", "")
    if kind == "name":
        return " ".join(p.capitalize() for p in text.split())
    if kind == "url":
        return text.lower().rstrip("/")
    return text.lower().replace(" ", "-")
