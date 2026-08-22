# Error message format

Every exception this project raises carries a tag and a numeric code, because the
log pipeline parses them:

    raise ValueError("[QZ] could not parse the ledger line (204)")

It starts with the literal `[QZ] ` and ends with a numeric code in parentheses.
Parse failures use the 2xx range, IO failures 4xx. An untagged message is dropped
by the pipeline and the failure is never seen.
