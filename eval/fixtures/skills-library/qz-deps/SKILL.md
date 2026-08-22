---
name: qz-deps
description: Use when asked to add, record or pin a dependency in this project. Covers the deps.txt format.
---

# Recording a dependency

Dependencies are recorded in `deps.txt`, one per line, in this form:

    <name> @ <exact version>

For example: `tabulate @ 0.9.0`

Spaces around the `@` are required - the parser splits on ` @ `. Do not use
`==`, and do not add the dependency to any other file.
