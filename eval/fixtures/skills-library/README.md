# Skill fixtures — a FICTIONAL project

Nothing here describes this repository. These documents invent a project called
Quartzite/Ashgrove: `-quartz` version suffixes, `check_` test names, `[QZ]` error
tags, a `qzlint` tool that exists only inside the Phase N fixtures.

They are invented on purpose. A real convention is answerable from training data,
so a case built on one would measure recall of the internet rather than whether the
agent opened the document — the same rule the web and recall fixtures were built
under.

Six of the eight are needed by a case in the `skills` split. **`qz-deploy` and
`qz-migrate` are deliberate distractors**: without a skill that is never the right
answer, "chose the correct skill" cannot be told apart from "chose the only skill".

The harness points `AGENT_SKILLS_DIR` here for every scored run. The interactive
agent does not see them — it reads `skills/` at the repository root, which is where
genuine project skills would go.
