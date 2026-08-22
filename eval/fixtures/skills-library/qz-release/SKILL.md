---
name: qz-release
description: Use when asked to cut, tag or prepare a release of this project. Covers the version suffix and the changelog line format.
---

# Cutting a release

Two files, both required, and the formats are project-specific.

1. `VERSION` holds the version with a `-quartz` suffix. Release 1.2.3 is written
   as `1.2.3-quartz`. Nothing else in the file.
2. `CHANGES.md` gains ONE new line at the end, in exactly this form:

   `rel <version-with-suffix> :: <one-line summary>`

   For example: `rel 1.2.3-quartz :: routing fixes`

Do not add headings, bullets or dates. The line is parsed by a script.
