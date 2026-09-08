"""A build pipeline. Reports each stage as it finishes, then keeps watching.

The artifact id is minted for this build and written to build.log. The pipeline
does not exit on its own.
"""
import secrets
import time

STAGES = ("resolve", "compile", "link", "package", "verify")
ARTIFACT = "ARTIFACT-" + secrets.token_hex(3).upper()

for index, stage in enumerate(STAGES):
    time.sleep(2)
    print(f"[{index + 1}/5] {stage} ok", flush=True)

print(f"artifact id: {ARTIFACT}", flush=True)
with open("build.log", "a", encoding="utf-8") as log:
    log.write(ARTIFACT + "\n")

while True:
    time.sleep(2)
    print("watching for changes", flush=True)
