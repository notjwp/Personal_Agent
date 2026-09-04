# Release conventions

Releases in this project are recorded in two places, both required.

- `VERSION` holds the version with a `-quartz` suffix: release 1.2.3 is written
  `1.2.3-quartz`, and nothing else goes in the file.
- `CHANGES.md` gains one line at the end, exactly
  `rel <version-with-suffix> :: <summary>`. For example:
  `rel 1.2.3-quartz :: parser rewrite`.
  No headings, no bullets, no dates - the line is parsed by a script.
