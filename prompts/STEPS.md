Output ONLY a numbered list of 2 to 6 steps.

No preamble, no reasoning, no explanation before or after the list. Start the
reply with "1." and nothing else.

Each step is one line, starts with a verb, and names the file it touches.
The last step says how you will know it worked - usually running the suite.

    1. Read tests/test_export.py to find the expected CSV shape
    2. Add a CSV writer beside the JSON one in ledger/export.py
    3. Register it in the exporter table
    4. Run pytest -q until the suite is green

Steps are what you WILL DO, not what you might consider. If the work is one
change to one file, say so in two steps rather than inventing four.
