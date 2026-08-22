---
name: qz-migrate
description: Use when asked to change the database schema, write a migration, or alter a table in this project.
---

# Schema migrations

1. Migrations live in `migrations/` and are named `<epoch>-<slug>.sql`.
2. Every migration needs a matching `.down.sql`.
3. Apply with `python tools/qzmigrate.py --forward`.
