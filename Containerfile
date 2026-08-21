# Sandbox image (NFR-204). The workspace is the only host mount; the scored run
# in Phase E adds --read-only --tmpfs /tmp and a COPY of the project.
FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Deps the practice projects need at RUNTIME, installed at BUILD time because the
# scored run has no network (NFR-205). pytest is pinned to the same major as the
# development host so host-side pre-verification is a valid proxy for the container.
RUN pip install --no-cache-dir "pytest>=9,<10" "flask>=3,<4"

# Agent dependencies. Exact pins: this project's headline number is a measurement,
# and an unpinned dependency silently changes what was measured.
RUN pip install --no-cache-dir "anthropic==0.122.0" "langgraph==1.2.11" "langgraph-checkpoint-sqlite==3.1.1" "openai==3.2.0"

# Test dependencies the REAL-repository cases declare for themselves (Phase J).
# Installed rather than staged as wheels: those cases are about fixing a bug, and
# every turn the agent spends on environment setup is free-tier quota spent on
# nothing. Must stay ahead of the pip.conf line below, like everything else here.
RUN pip install --no-cache-dir "freezegun" "attrs" "pygments" "markdown-it-py"

# The `missing-dep` case must be solvable with networking off: stage the wheel
# but deliberately do NOT install it.
RUN pip download --no-cache-dir tabulate -d /wheels

# Point pip at that local directory. THIS MUST BE THE LAST PIP-RELATED STEP:
# after it, no RUN can reach PyPI. A plain `pip install tabulate` now works
# under --network none, so the agent needs no knowledge of the wheelhouse.
RUN printf '[global]\nno-index = true\nfind-links = /wheels\n' > /etc/pip.conf

ENV AGENT_WORKSPACE=/workspace \
    AGENT_HOME=/state \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_USER=1 \
    PYTHONUSERBASE=/tmp/pyuser

# The two writable roots, and nothing else (Phase K). The harness bind-mounts the
# project READ-ONLY at /app, so anything that must be written needs a declared
# home of its own:
#   /workspace  the task, wiped between runs by reset.sh
#   /state      checkpoints now, memory and skills later - it exists precisely
#               BECAUSE the workspace is wiped, and must outlive it
RUN mkdir -p /workspace /state
WORKDIR /app

# Why PIP_USER + PYTHONUSERBASE (Phase E2, measured before adopting):
#
# The scored suite runs --read-only, which makes site-packages immutable. That
# would make `missing-dep` UNSOLVABLE, because its fix is a plain `pip install`.
# Measured on this image:
#   plain pip install under --read-only -> OSError: Read-only file system,
#     /root/.local -- pip already falls back to a user install, but /root is
#     read-only too, so the fallback fails as well
#   with these two vars + --tmpfs /tmp  -> installs and imports from
#     /tmp/pyuser/lib/python3.12/site-packages
#   pre-installed packages              -> still import; the user site is
#     ADDITIVE, not a replacement
#
# A tmpfs mounted OVER site-packages would have masked pytest/flask/langgraph
# and broken all five cases instead of fixing one. Measured, not assumed.
#
# They live in the image, not in the harness `docker run`, so the CLI, a manual
# run and the harness all get the same environment.

# No COPY: the project is bind-mounted during Phase A for fast iteration.
# Phase E adds COPY + .dockerignore for the sealed image used in scored runs.
