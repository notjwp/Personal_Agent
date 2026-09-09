"""Project tooling. Not documentation.

The first line matters: `skills.extract` writes a skill from every document the
agent READ and did not edit, and `_when` builds its description from that line.
Measured 2026-09-09 - this file opened with "Cut a release" and the skill made
from it out-matched the conventions document the case exists to test.

The accepted suffix is compared as a DIGEST so this file does not name it.
"""
import hashlib
import pathlib
import sys

ACCEPTED = "4330fe0e43cfb2c45b6a262de2f7690965feaa37311092778e6b48d7c7b23f7e"

# A DICTIONARY word here is the fixture containing its own answer: measured
# 2026-09-09, the agent ran `echo -n slate | sha256sum` against this constant and
# was one wordlist away from the suffix without ever opening the runbook. A
# high-entropy token cannot be recovered from the digest, so the only route to it
# is the document - which is what this case exists to measure.

version = pathlib.Path("VERSION").read_text(encoding="utf-8").strip()
suffix = version.rsplit("-", 1)[-1] if "-" in version else ""

if hashlib.sha256(suffix.encode("utf-8")).hexdigest() != ACCEPTED:
    print("refused: VERSION is not in the form this project releases.")
    sys.exit(1)

pathlib.Path("released.txt").write_text(f"released {version}\n", encoding="utf-8")
print(f"released {version}")
