# qzlint codes

| code | meaning | the fix |
|---|---|---|
| QZ104 | a module is missing its owner banner | make `# owner: unassigned` the FIRST line of the file, keeping everything else |
| QZ103 | a line carrying a TODO has not been marked as reviewed | append ` # qz-ok` to the end of that line, keeping the TODO |
