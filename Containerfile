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

# The `missing-dep` case must be solvable with networking off: stage the wheel
# but deliberately do NOT install it.
RUN pip download --no-cache-dir tabulate -d /wheels

# Point pip at that local directory. THIS MUST BE THE LAST PIP-RELATED STEP:
# after it, no RUN can reach PyPI. A plain `pip install tabulate` now works
# under --network none, so the agent needs no knowledge of the wheelhouse.
RUN printf '[global]\nno-index = true\nfind-links = /wheels\n' > /etc/pip.conf

ENV AGENT_WORKSPACE=/workspace \
    AGENT_HOME=/app/.agent \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN mkdir -p /workspace /app/.agent
WORKDIR /app

# No COPY: the project is bind-mounted during Phase A for fast iteration.
# Phase E adds COPY + .dockerignore for the sealed image used in scored runs.
