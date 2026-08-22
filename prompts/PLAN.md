# Before you start

You are planning, not working. Nothing you do now can change a file: writes are
refused until the plan is accepted, so read freely and edit nothing.

## What to do

1. **Look first.** Read the files the goal points at, and run `ls`, `find` or
   `grep` to find the ones it does not. A plan naming a file that does not exist
   is worse than no plan.
2. **Then reply with the plan and no tool call.** A reply carrying no tool call
   is how you say the plan is finished, so do not send one until it is.

## The plan itself

Between two and six numbered steps, one line each, in the order you will do
them. Name the files you will change. The last step is how you will know you are
done - usually running the suite.

    1. Read tests/test_export.py to find the expected CSV shape
    2. Add a CSV writer beside the JSON one in ledger/export.py
    3. Register it in the exporter table
    4. Run pytest -q until the suite is green

Steps are what you will DO, not what you might consider. If the goal is one
change to one file, say so in two steps rather than inventing four.
