#!/usr/bin/env bash
# Restore the workspace to a practice project's known-broken state.
#
# Idempotent: running twice produces an identical tree. Removes untracked files
# and dotfiles, because the agent creates both and `git checkout` would leave
# them behind — letting one case contaminate the next.
set -euo pipefail

: "${AGENT_WORKSPACE:?AGENT_WORKSPACE must be set (its only default lives in agent/config.py)}"
case_id="${1:?usage: reset.sh <case-id>}"

src="$(cd "$(dirname "$0")/.." && pwd)/eval/fixtures/${case_id}"
[ -d "$src" ] || { echo "no such practice project: ${case_id}" >&2; exit 1; }
[ -d "$AGENT_WORKSPACE" ] || { echo "workspace does not exist: ${AGENT_WORKSPACE}" >&2; exit 1; }

# -mindepth 1 wipes the CONTENTS while preserving the mount point itself.
# This glob-free form also catches dotfiles such as .agent/ spill directories.
find "$AGENT_WORKSPACE" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

cp -a "${src}/." "$AGENT_WORKSPACE/"
