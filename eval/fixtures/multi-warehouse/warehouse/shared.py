"""Shared helpers. These are correct."""


def label(name, n):
    return name + " (" + str(n) + ")"


def is_blank(text):
    return not text.strip()
