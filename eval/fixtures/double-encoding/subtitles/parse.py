"""Turning subtitle text into cues."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Cue:
    index: int
    text: str


def parse(raw: str):
    """One cue per blank-line-separated block."""
    cues = []
    blocks = [b for b in raw.split(chr(10) * 2) if b.strip()]
    for block in blocks:
        lines = block.strip().split(chr(10))
        cues.append(Cue(index=int(lines[0]), text=" ".join(lines[2:])))
    return cues
