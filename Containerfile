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

# MCP (Phase L). The client half plus the ONE server this phase ships.
#
# Baked at BUILD time because /etc/pip.conf below sets no-index: nothing can be
# installed at run time, so every server must be here or it does not exist. That
# is also the security property - a server cannot be introduced mid-run.
#
# mcp is pinned to 1.29.0 and NOT 2.0.0. Measured, not assumed: 2.0.0 fails with
# `ResolutionImpossible`, because mcp-server-fetch declares `mcp<2,>=1.29.0`.
RUN pip install --no-cache-dir "mcp==1.29.0" "mcp-server-fetch==2026.8.18"

# Skills (Phase N). The agentskills.io frontmatter is YAML, and `description` is
# the exact field retrieval matches on - a split(":", 1) parser breaks on a quoted
# or folded value and would DEGRADE matching rather than fail loudly, which is the
# worst of both. Baked here for the same reason as mcp: pip.conf below sets
# no-index, so nothing installs at run time.
RUN pip install --no-cache-dir "pyyaml==6.0.2"

# The TUI (FR-701, "Provide a CLI/TUI chat with streamed output"). Baked here
# for the same reason as everything above: pip.conf below sets no-index.
#
# textual pulls in rich, and eval/fixtures/real-rich IS the rich source tree
# (vendored at 14.3.4). Checked rather than assumed before adding it: that
# fixture has tests/__init__.py, so pytest's prepend import mode puts /workspace
# ahead of site-packages and `import rich` there resolves to the vendored copy.
# The pins differ ON PURPOSE - 14.3.3 here against 14.3.4 vendored - which makes
# the check decisive: inside that workspace, rich.__version__ must read 14.3.4.
#
# Precedent agrees: pygments and markdown-it-py are already installed above FOR
# real-rich, alongside its vendored copy of the same names.
RUN pip install --no-cache-dir "textual==8.0.1" "rich==14.3.3"

# Web search (FR-501). Keyless on purpose: ddgs needs no account and no API key,
# so a scored run reproduces without a subscription that might lapse. Hermes
# supports nine providers behind a plugin layer and all but ddgs/searxng need a
# key - the CHOICE is what was taken from it, not the layer.
#
# Baked here for the same reason as everything above: pip.conf below sets
# no-index, so nothing installs at run time.
RUN pip install --no-cache-dir "ddgs==9.16.0"

# Semantic retrieval was built here and REVERTED. onnxruntime, tokenizers,
# numpy and bge-small-en-v1.5 added 450 MB and, measured on the six recall
# pairs, changed the score by nothing: 5/6 fused, 5/6 without. The win was in
# the QUERY - see memory._terms. eval/CHANGELOG.md carries the ablation.

# The `missing-dep` case must be solvable with networking off: stage the wheel
# but deliberately do NOT install it.
RUN pip download --no-cache-dir tabulate -d /wheels

# Point pip at that local directory. THIS MUST BE THE LAST PIP-RELATED STEP:
# after it, no RUN can reach PyPI. A plain `pip install tabulate` now works
# under --network none, so the agent needs no knowledge of the wheelhouse.
RUN printf '[global]\nno-index = true\nfind-links = /wheels\n' > /etc/pip.conf

# FR-205 wants git status/diff/branch/add/commit. `git commit` FAILS without an
# identity, and the failure ("Please tell me who you are") looks like the agent
# doing something wrong rather than the image missing two lines. Checked, not
# assumed: user.email was unset until 2026-08-23.
#
# safe.directory because the workspace is a bind mount owned by a different uid
# on the host, which git otherwise refuses to operate on as "dubious ownership".
RUN git config --system user.email "agent@localhost" \
 && git config --system user.name  "Personal Agent" \
 && git config --system init.defaultBranch main \
 && git config --system --add safe.directory '*'

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
