"""A build pipeline. It stops at each gate and waits to be released, and once
the build is done it keeps running, watching for changes."""
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

# The log BEFORE the announcement. The other way round, a driver that stops
# the build the moment it sees the id can lose the log, and the check reads
# the log - measured 2026-09-13, three runs with the right id scored wrong.
with (HERE / "build.log").open("a", encoding="utf-8") as log:
    log.write(ARTIFACT + "\n")
print(f"artifact id: {ARTIFACT}", flush=True)

# Still running is part of the job, and the heartbeat is how the check knows.
while True:
    (HERE / "heartbeat").write_text(str(time.time()), encoding="utf-8")
    time.sleep(2)
    print("watching for changes", flush=True)
