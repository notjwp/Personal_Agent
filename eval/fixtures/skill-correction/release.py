"""Cut a release. Refuses anything VERSION is not allowed to say.

The accepted suffix is compared as a DIGEST on purpose: a checker that names it
in its own source is a fixture containing its own answer, and this case only
measures anything while the suffix lives in CONVENTIONS.md and nowhere else.
"""
import hashlib
import pathlib
import sys

ACCEPTED = "1a4620b9b4790cd3182466af7d10adb92ba9bea493be38d6450be11b4ad5094d"

version = pathlib.Path("VERSION").read_text(encoding="utf-8").strip()
suffix = version.rsplit("-", 1)[-1] if "-" in version else ""

if hashlib.sha256(suffix.encode("utf-8")).hexdigest() != ACCEPTED:
    print("refused: VERSION is not in the form this project releases.")
    sys.exit(1)

pathlib.Path("released.txt").write_text(f"released {version}\n", encoding="utf-8")
print(f"released {version}")
