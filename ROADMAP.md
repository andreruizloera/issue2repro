# Roadmap

Concrete future work, roughly in priority order. None of this is
implemented today; the README only documents what works now.

## More ecosystems

- Rust (Cargo.toml, `cargo test`), Go (go.mod, `go test ./...`), Ruby
  (Gemfile, `rake test` / `rspec`), Java (pom.xml / build.gradle).
- Recognize their stack trace formats in issue text (Rust panics, Go
  goroutine dumps, JVM `at pkg.Class.method(File.java:12)` frames).

## Smarter environment inference

- Pin the Python or Node version from `requires-python`, `.python-version`,
  `.nvmrc`, or `engines` in package.json, and pick the matching Docker
  base image instead of a fixed default.
- Detect lockfiles (uv.lock, poetry.lock, package-lock.json, pnpm-lock.yaml)
  and prefer the matching installer for faithful dependency resolution.
- Read OS or service hints from the issue text ("only on Ubuntu",
  "requires postgres") and surface them in metadata.json plus a
  docker-compose.yml when a service is named.

## Better issue understanding

- Extract version numbers from the issue ("broken since 2.1.0") and check
  out the matching tag instead of the default branch head.
- Follow issue references (#123, linked PRs) and merge their signals.
- Parse `pip freeze` / `npm ls` blocks pasted into issues and pin those
  exact versions in the generated environment.

## Reproduction verification

`issue2repro verify` shipped: it runs reproduce.sh in the generated
Docker image (container by default, host only with `--no-docker`),
compares the failure it observed against the one the issue describes
(exception type, message, the innermost stack frame, and the failing
tests, which it reads from pytest, unittest, go test, cargo test, and
node --test), and reports a verdict beside the inferred confidence score
rather than overwriting it. What is still open:

- Cache the built image and the verdict, keyed by the clone's HEAD and
  the step plan, so re-verifying an issue after a fix costs one run
  instead of two.
- Read stack frames from the runners whose per-test output verify already
  understands: `go test` panics, Rust panic locations, and JVM frames
  would give those projects the frame comparison Python and Node have.
- Treat a target test that has been deleted or renamed as its own
  verdict, instead of folding it into "no per-test failures to compare".
- Optional `--network none` on the container for projects whose
  reproduction needs no installs, so the run cannot reach the internet at
  all.

## Quality of life

- `issue2repro batch` over every open issue with a `bug` label, ranking
  the tracker by reproduction confidence.
- Markdown report output for pasting back into the issue thread.
- Authentication fallback to unauthenticated `api.github.com` requests
  for public repositories when gh is not installed.
