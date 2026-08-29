# Summarise these turns

They are being removed from a conversation to save room, and your summary
replaces them. Everything not in your summary is lost to the agent that
continues the work.

Retain exactly these, and nothing else:

- **Decisions made** — what was concluded, and what was ruled out.
- **Files touched** — every path read or changed, with what changed.
- **Commands that worked** — verbatim, so they can be re-run. Not ones that failed.
- **Errors hit** — the message, and whether it was resolved.
- **Artifact paths** — any `/workspace/.agent/artifacts/...` path mentioned, so
  the full output can still be read back.

Write it as terse notes, not prose. No preamble, no "the assistant then". Facts
the next turn needs, in the order they happened. Omit anything that was tried and
abandoned unless the outcome matters.
