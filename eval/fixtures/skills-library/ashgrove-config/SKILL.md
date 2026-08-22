---
name: ashgrove-config
description: Use when asked to create or edit a config file for an Ashgrove service. Covers the required fields and their order.
---

# Ashgrove service configs

Every Ashgrove service config is derived from the bundled template.

1. Read `template.conf` (bundled with this skill).
2. Copy it exactly, then replace `<SERVICE>` with the service name.
3. Field order matters - the loader reads positionally, not by key.

Do not invent fields. A config missing `ashgrove_channel` will not load.
