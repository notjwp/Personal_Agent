---
name: qz-errors
description: Use when asked to add error handling, raise an exception, or improve an error message in this project. Covers the required message format.
---

# Error message format

Every exception raised by this project carries a tag and a numeric code, because
the log pipeline parses them:

    raise ValueError("[QZ] could not parse the ledger line (204)")

- Starts with the literal `[QZ] `
- Ends with a numeric code in parentheses
- Parse failures use the 2xx range; IO failures use 4xx

An untagged message is dropped by the pipeline and the failure is never seen.
