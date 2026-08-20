"""Slug normalisation."""
import re


def slugify(title: str) -> str:
    """A url-safe slug: lowercase, words joined by hyphens."""
    cleaned = re.sub(r"[^A-Za-z0-9 ]+", "", title)
    return "-".join(cleaned.split())
