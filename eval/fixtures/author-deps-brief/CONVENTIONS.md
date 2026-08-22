# Recording dependencies

Dependencies go in `deps.txt`, one per line, as `<name> @ <exact version>` - for
example `tabulate @ 0.9.0`. The spaces around the `@` are required, the parser
splits on ` @ `. Never use `==`, and do not add the dependency anywhere else.
