---
name: qz-lint
description: Use when asked to fix style, add a module header, or clean up lint in this project. Covers the header every module must carry.
---

# Module headers

Every `.py` module in this project must open with an ownership banner as its
**first** line, before imports and before any docstring. The exact form is
project-specific and is listed in `codes.md` under QZ104.

Add the banner, keep everything else in the file unchanged, and do not delete
code to silence a warning - none of these codes are fixed by removing a line.
