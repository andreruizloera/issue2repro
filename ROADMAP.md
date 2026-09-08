# Roadmap

Concrete future work, roughly in priority order. None of this is
implemented today; the README only documents what works now.

## More ecosystems

- Rust (Cargo.toml, `cargo test`), Go (go.mod, `go test ./...`), Ruby
  (Gemfile, `rake test` / `rspec`). This is about DETECTING the project
  and building its environment: Go and Rust stack traces are already read
  out of issue text, but a Go or Rust repository is still not detected,
  gets no Dockerfile, and gets no test command inferred for it. JVM
  detection shipped, for Maven and Gradle.
- Recognize the stack trace formats still unread: Ruby backtraces, and C
  or C++ backtraces that were symbolized. JVM frames shipped.
- Scope a Gradle workspace's fallback test command to the implicated test
  class. Maven is scoped today and Gradle deliberately is not, because a
  `--tests` filter matching nothing fails the build and Gradle offers no
  command-line way to disable that, so a bad guess would read as a
  reproduction. Doing it safely probably means reading the test source
  set to confirm the class exists before adding the filter.
- Pre-resolve JVM dependencies in a Dockerfile layer, so a rebuild does
  not re-download them, and so a resolution failure lands in `docker
  build` where it is reported as an environment failure. Today it lands
  inside the reproduction step, where the honest verdict is `unknown`
  rather than the more precise `environment-failure` the Python path
  gives. No install step is generated because both build tools resolve
  inside the task they run and a warm-up goal (`dependency:go-offline`)
  fails on some real projects.
- Read an `exceptionFormat "full"` Gradle block on the ISSUE side. A RUN's
  output in that format is already read by the general exception scanner,
  message included; an issue pasting the same block yields no exception at
  all, so the two sides parse one format differently. Gradle's DEFAULT
  output is read from both sides and that is what the end-to-end example
  relies on. Wiring up the full format was tried and reverted: the
  issue-side reader is anchored on the line under the first `FAILED` line
  while the run side keeps the last exception it sees, so a log with
  several failing tests of different types would compare two different
  exceptions and downgrade a real reproduction to `PARTIAL`. Doing it
  safely means making both sides agree on WHICH failure they describe,
  which is a bigger change than the reader itself.
- Verify a committed Gradle workspace with a WRAPPER end to end.
  `examples/gradleshop` has no `gradlew`, so it exercises the
  `gradle:8-jdk21` image and the `gradle` on PATH. The wrapper path picks
  `eclipse-temurin:21-jdk` and is still generated but never run, because a
  wrapper project only works if its JAR is committed and this repository
  does not vendor binaries. Doing it means finding a way to produce the
  wrapper at demo time rather than committing it.
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
- Read the stack frames out of a Gradle run configured with
  `testLogging { exceptionFormat "full" }`. Gradle indents the exception
  header under its `FAILED` line and the JVM trace extractor anchors a
  header at column zero, so the frames underneath currently attach to no
  trace. Measured on a real run: the type and message are read, the
  frames are not. The fix is a positionally anchored header, the same
  shape as `gradle_exception`, rather than loosening the column-zero
  anchor for every language.
- Ruby backtraces, now the last mainstream runtime whose stack is unread.

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
