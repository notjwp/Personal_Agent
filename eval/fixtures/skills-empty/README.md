# An empty skill library, on purpose

The `authoring` split starts here. Phase O measures the agent building its OWN
library from nothing, so it must begin with none — and its first control run
proved why: pointed at Phase N's library it loaded the human-written `qz-release`
skill, which already taught the convention the case was invented to test.

Skills the agent writes land in the agent home (`/state/skills/`), which
`catalogue()` reads as its second root. This directory stays empty.
