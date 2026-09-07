# Roadmap

Concrete future work, roughly in priority order. None of this is
implemented today; the README only documents what works now.

## More ecosystems

- Rust (Cargo.toml, `cargo test`), Go (go.mod, `go test ./...`), Ruby
  (Gemfile, `rake test` / `rspec`), Java (pom.xml / build.gradle). This
  is about DETECTING the project and building its environment: Go and
  Rust stack traces are already read out of issue text, but a Go or Rust
  repository is still not detected, gets no Dockerfile, and gets no test
  command inferred for it.
- Recognize the stack trace formats still unread: Ruby backtraces, and C
  or C++ backtraces that were symbolized. JVM frames shipped.
- Read Maven Surefire and Gradle failure summaries for JVM test names.
  Only the JUnit console launcher's `Failures (N):` block is read today,
  because that is the one whose output could be captured here, and every
  pattern in this parser is written against a real run rather than from
  memory.
- Read a Go `t.Errorf` location (`tax_test.go:7: got 107, want 110`) as a
  failure location. It is deliberately not read as a frame today because
  the pattern is close to ordinary prose; doing it safely probably means
  requiring the `--- FAIL:` block it sits under.

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
- Ruby backtraces, the last mainstream runtime whose stack is unread. Go
  panics, Rust panics, and JVM exceptions shipped.
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
