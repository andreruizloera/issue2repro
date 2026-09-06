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
  yourself: it is arbitrary code execution by design.
- `verify` is the containerized path and is the one to prefer for code
  you do not trust. It builds the generated Dockerfile and runs
  `reproduce.sh` inside the image. When Docker is missing it stops with
  an error naming `--no-docker`; it never silently falls back to running
  repository code on the host. `--no-docker` is opt-in and prints the
  same warning `run` does.
- The container is not a security boundary against a determined attacker.
  It runs with Docker's defaults and with network access, because
  reproductions install dependencies. It is a boundary against a
  reproduction that trashes your working directory, not against a
  container escape.
- The verify image tag is derived from the issue's owner, repo, and
  number with every character Docker does not accept replaced, so issue
  metadata cannot inject arguments into the `docker build` command line.
  Docker and git are invoked with fixed argument lists, never through a
  shell.
- `ISSUE2REPRO_TRACE` only makes `reproduce.sh` print step markers. The
  script's behavior is identical with and without it.
- For Python projects, `reproduce.sh` installs into a workspace-local
  virtualenv so issue-supplied `pip install` lines do not modify your
  system or user environment. This limits pollution, not malice.
- The gh CLI is invoked with fixed argument lists (no shell
  interpolation of issue content).

## Reporting

Open a GitHub issue for anything that is not sensitive. For something
exploitable, email andre.x.ruizloera@gmail.com and it will be looked at
quickly.
