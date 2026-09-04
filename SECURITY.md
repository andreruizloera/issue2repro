# Security

## Threat model

issue2repro consumes untrusted input on two fronts:

1. Issue text written by anyone on the internet.
2. Repository code cloned from the issue's project.

## What the tool does about it

- `inspect` and `build` never execute repository code or issue-derived
  commands. They clone, read files, and write a workspace. Extraction is
  pure string processing; manifest parsing uses `tomllib` and `json`
  only.
- Commands from the issue are written into `reproduce.sh`, never
  executed during analysis. The script carries an explicit warning
  header and is meant to be read before it is run.
- `run` executes `reproduce.sh` on your machine and says so loudly
  first. Treat it exactly like running that repository's test suite
  yourself: it is arbitrary code execution by design. Prefer building
  the generated Dockerfile and running inside the container when you do
  not trust the project.
- For Python projects, `reproduce.sh` installs into a workspace-local
  virtualenv so issue-supplied `pip install` lines do not modify your
  system or user environment. This limits pollution, not malice.
- The gh CLI is invoked with fixed argument lists (no shell
  interpolation of issue content).

## Reporting

Open a GitHub issue for anything that is not sensitive. For something
exploitable, email andre.x.ruizloera@gmail.com and it will be looked at
quickly.
