# tinycalc (demo fixture)

A deliberately buggy toy calculator. It exists only so the issue2repro
demo can run fully offline: `demo.sh` copies this directory into a
temporary git repository and clones it over `file://`.

The bug is real and kept on purpose: `evaluate("2 + -3")` raises
`ValueError` because the tokenizer splits `-3` into `-` and `3`.
The matching bug report lives at `../tinycalc-issue-1.json`.
