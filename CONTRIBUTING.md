# Contributing

Contributions are welcome. The codebase is small on purpose, so most
changes should stay small too.

## Setup

```bash
git clone https://github.com/andreruizloera/issue2repro
cd issue2repro
uv venv --python 3.13
uv pip install -e ".[dev]"
```

## Before opening a pull request

```bash
.venv/bin/ruff format src tests examples
.venv/bin/ruff check src tests examples
.venv/bin/python -m pytest
./demo.sh
```

All four must pass. The demo runs fully offline, so no GitHub token or
network access is needed for development.

## Ground rules

- Tests never hit live GitHub. New extraction or detection behavior gets
  a committed fixture under `tests/fixtures/` (issue payloads) or a
  project layout built in `tmp_path`.
- The confidence score must stay honest: every component needs a reason
  string that says exactly what was checked, and new components need
  tests for both the earned and unearned cases.
- Keep error handling clean: expected failures raise `Issue2ReproError`
  with a message a user can act on, never a raw traceback.
- Full type annotations on new code.

## Adding a language

`detect.py` is the place to start: add manifest detection and a test
command heuristic, then a Docker base image in `workspace.py` and
fixtures for the new ecosystem. See ROADMAP.md for candidates.
