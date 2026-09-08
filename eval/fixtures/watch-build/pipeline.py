"""A build pipeline. It stops at each gate and waits to be released."""
import pathlib
import secrets
import time

HERE = pathlib.Path(__file__).resolve().parent
ARTIFACT = "ARTIFACT-" + secrets.token_hex(3).upper()

for gate in (1, 2, 3):
    password = secrets.token_hex(3).upper()
    print(f"GATE {gate} is holding. To release it, create the file "
          f"gate-{gate}.txt containing exactly {password}", flush=True)
    reply = HERE / f"gate-{gate}.txt"
    while not (reply.exists()
               and reply.read_text(encoding="utf-8").strip() == password):
        time.sleep(0.5)
    print(f"gate {gate} released", flush=True)

print(f"artifact id: {ARTIFACT}", flush=True)
with (HERE / "build.log").open("a", encoding="utf-8") as log:
    log.write(ARTIFACT + "\n")

while True:
    time.sleep(2)
    print("watching for changes", flush=True)
