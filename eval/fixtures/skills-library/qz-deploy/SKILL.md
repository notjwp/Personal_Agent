---
name: qz-deploy
description: Use when asked to deploy this project to staging or production, or to roll a deployment back.
---

# Deploying

1. `make ship-quartz` builds and uploads the artefact.
2. Watch `ashgrove-deploy status` until it reports `settled`.
3. Roll back with `make ship-quartz ROLLBACK=1`.
