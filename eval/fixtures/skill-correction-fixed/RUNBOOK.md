# Release checklist

Releases are cut from the working tree with the bundled script.

1. Write the new version into `VERSION`, followed by this project's suffix.
   The suffix is `-quartz`, so version 2.0 is written as `2.0-quartz`.
2. Run `python release.py`.
3. The script writes `released.txt` when it accepts the version.
