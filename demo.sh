#!/usr/bin/env bash
# Demo: run the full issue2repro pipeline against the committed fixture
# project in examples/tinycalc and the committed bug reports in
# examples/tinycalc-issue-*.json.
#
# Analysis and workspace generation are offline: the issues are read from
# local JSON files and the repository is cloned over file://. The parts that
# EXECUTE a reproduction install the project's test dependencies, so those
# reach the network exactly like a real reproduction does.
#
# Every line this script checks is a line the README pastes. If the tool's
# output drifts from the docs, this exits nonzero.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v issue2repro >/dev/null 2>&1; then
    I2R=(issue2repro)
elif python3 -c "import issue2repro" >/dev/null 2>&1; then
    I2R=(python3 -m issue2repro.cli)
else
    echo "issue2repro is not installed. Run: uv venv --python 3.13 && uv pip install -e ." >&2
    exit 1
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# The verify parts run on the host by default so the demo works anywhere.
# Set DEMO_VERIFY_DOCKER=1 to run them in a real container instead, which is
# what CI does. Every verdict checked below is identical either way.
# A plain string, not an array: bash 3.2 (the macOS default) treats an empty
# array as unbound under `set -u` and kills the script.
HOST_FLAG="--no-docker"
if [ "${DEMO_VERIFY_DOCKER:-}" = "1" ]; then
    HOST_FLAG=""
fi

# Which path a verdict line must name. Without this the container job would
# pass unchanged if DEMO_VERIFY_DOCKER stopped having any effect, because
# every other line it checks is the same on both paths: a check that cannot
# fail. `verify` prints where it ran, so the demo asserts it ran there.
where_clause() {
    # where_clause <image tag slug>
    if [ "${DEMO_VERIFY_DOCKER:-}" = "1" ]; then
        printf 'observed by running reproduce.sh in Docker (issue2repro-verify:%s)' "$1"
    else
        printf 'observed by running reproduce.sh on this machine'
    fi
}

FAILURES=0

check() {
    # check <file> <expected substring>
    if ! grep -qF -- "$2" "$1"; then
        echo "DEMO CHECK FAILED: expected to find in $(basename "$1"): $2" >&2
        FAILURES=$((FAILURES + 1))
    fi
}

check_exit() {
    # check_exit <actual> <expected> <label>
    if [ "$1" != "$2" ]; then
        echo "DEMO CHECK FAILED: $3 exited $1, expected $2" >&2
        FAILURES=$((FAILURES + 1))
    fi
}

# Turn the committed fixture into a standalone git repo so it can be
# shallow-cloned exactly like a real project.
cp -R "$ROOT/examples/tinycalc" "$WORK/tinycalc"
git -C "$WORK/tinycalc" init --quiet
git -C "$WORK/tinycalc" add --all
git -C "$WORK/tinycalc" -c user.name=fixture -c user.email=fixture@example.invalid \
    commit --quiet --message "tinycalc demo fixture"

CLONE="file://$WORK/tinycalc"

echo "== issue2repro build =="
"${I2R[@]}" build "https://github.com/example/tinycalc/issues/1" \
    --issue-file "$ROOT/examples/tinycalc-issue-1.json" \
    --clone-url "$CLONE" \
    --output "$WORK/repro" | tee "$WORK/build.txt"

check "$WORK/build.txt" "Issue: example/tinycalc#1: evaluate() crashes on expressions with negative numbers"
check "$WORK/build.txt" "Language: python (pyproject.toml)"
check "$WORK/build.txt" "Install: pip install -e ."
check "$WORK/build.txt" "Test command: python -m pytest"
check "$WORK/build.txt" "  stack trace: python, 2 frame(s) (ValueError: invalid literal for int() with base 10: '-')"
check "$WORK/build.txt" "  filenames mentioned: tests/test_evaluate.py, src/tinycalc/evaluate.py"
check "$WORK/build.txt" "Reproduction confidence: 100% inferred"
check "$WORK/build.txt" "[35/35] explicit repro commands: 2 command(s) found in fenced shell blocks"
check "$WORK/build.txt" "[25/25] stack trace: python stack trace maps to existing file(s): tests/test_evaluate.py, src/tinycalc/evaluate.py"
check "$WORK/build.txt" "[20/20] test command: detected from manifests: python -m pytest"
check "$WORK/build.txt" "[20/20] language detected: python (manifests: pyproject.toml)"
check "$WORK/build.txt" "  .dockerignore"

echo
echo "== workspace tree =="
(cd "$WORK/repro" && find . -not -path "./source/.git" -not -path "./source/.git/*" | sort)

echo
echo "== generated reproduce.sh =="
cat "$WORK/repro/reproduce.sh"
check "$WORK/repro/reproduce.sh" "# Step markers for \`issue2repro verify\`, so it can tell a setup failure"
check "$WORK/repro/reproduce.sh" '    if [ "${ISSUE2REPRO_TRACE:-}" = "1" ]; then'
check "$WORK/repro/reproduce.sh" "i2r_step 1 setup"
check "$WORK/repro/reproduce.sh" "pip install pytest"
check "$WORK/repro/reproduce.sh" "i2r_step 2 repro"
check "$WORK/repro/reproduce.sh" "python -m pytest tests/test_evaluate.py -x"

echo
echo "== issue2repro run (exit code only: is a nonzero exit the reported bug?) =="
code=0
"${I2R[@]}" run "https://github.com/example/tinycalc/issues/1" \
    --issue-file "$ROOT/examples/tinycalc-issue-1.json" \
    --clone-url "$CLONE" \
    --output "$WORK/repro" | tail -n 3 | tee "$WORK/run.txt" || code=$?
check "$WORK/run.txt" "a nonzero exit usually means the reported failure reproduced."

echo
echo "== issue2repro verify: the reported failure, checked against the run =="
code=0
"${I2R[@]}" verify "https://github.com/example/tinycalc/issues/1" \
    --issue-file "$ROOT/examples/tinycalc-issue-1.json" \
    --clone-url "$CLONE" \
    --output "$WORK/repro" \
    $HOST_FLAG >"$WORK/v1.txt" 2>&1 || code=$?
sed -n '/^Verification:/,$p' "$WORK/v1.txt"
check_exit "$code" 0 "verify (reproduced)"
check "$WORK/v1.txt" "Verification: REPRODUCED ($(where_clause example-tinycalc-1))"
check "$WORK/v1.txt" "the run failed the way the issue describes"
check "$WORK/v1.txt" "exception: match"
check "$WORK/v1.txt" "message:   exact"
check "$WORK/v1.txt" "  expected (from the issue): ValueError: invalid literal for int() with base 10: '-'; test_negative_operand"
check "$WORK/v1.txt" "  observed (from the run):   ValueError: invalid literal for int() with base 10: '-'; tests/test_evaluate.py::test_negative_operand"
check "$WORK/v1.txt" "frames:    match (evaluate in src/tinycalc/evaluate.py)"
check "$WORK/v1.txt" "tests:     match (test_negative_operand)"

echo
echo "== issue2repro verify: the same bug, but the reporter's setup step fails =="
code=0
"${I2R[@]}" verify "https://github.com/example/tinycalc/issues/2" \
    --issue-file "$ROOT/examples/tinycalc-issue-2.json" \
    --clone-url "$CLONE" \
    --output "$WORK/repro2" \
    --no-docker >"$WORK/v2.txt" 2>&1 || code=$?
sed -n '/^Verification:/,$p' "$WORK/v2.txt"
check_exit "$code" 2 "verify (environment failure)"
check "$WORK/v2.txt" "Verification: ENVIRONMENT-FAILURE"
check "$WORK/v2.txt" "the script exited during setup, at step 1 (pip install -r requirements-dev.txt)."
check "$WORK/v2.txt" "The reproduction never ran, so this says nothing about the bug."
check "$WORK/v2.txt" "  expected (from the issue): ValueError: invalid literal for int() with base 10: '-'; test_negative_operand"
check "$WORK/v2.txt" "  observed (from the run):   nothing checkable"

echo
echo "== issue2repro verify: the suite fails, but not the way this issue says =="
code=0
"${I2R[@]}" verify "https://github.com/example/tinycalc/issues/3" \
    --issue-file "$ROOT/examples/tinycalc-issue-3.json" \
    --clone-url "$CLONE" \
    --output "$WORK/repro3" \
    --no-docker >"$WORK/v3.txt" 2>&1 || code=$?
sed -n '/^Verification:/,$p' "$WORK/v3.txt"
check_exit "$code" 1 "verify (different failure)"
check "$WORK/v3.txt" "Verification: DIFFERENT-FAILURE"
check "$WORK/v3.txt" "the run failed for a different reason than the issue reports"
check "$WORK/v3.txt" "  expected (from the issue): ZeroDivisionError: integer division or modulo by zero; test_divide_by_zero"
check "$WORK/v3.txt" "  observed (from the run):   ValueError: invalid literal for int() with base 10: '-'; tests/test_evaluate.py::test_negative_operand"
check "$WORK/v3.txt" "exception: mismatch"
check "$WORK/v3.txt" "tests:     mismatch"
check "$WORK/v3.txt" "note: the issue reports ZeroDivisionError, the run raised ValueError"
check "$WORK/v3.txt" "note: the issue points at test_divide_by_zero, but the failing test(s) were tests/test_evaluate.py::test_negative_operand"

echo
echo "== issue2repro verify: the same exception, raised somewhere else =="
code=0
"${I2R[@]}" verify "https://github.com/example/tinycalc/issues/4" \
    --issue-file "$ROOT/examples/tinycalc-issue-4.json" \
    --clone-url "$CLONE" \
    --output "$WORK/repro4" \
    --no-docker >"$WORK/v4.txt" 2>&1 || code=$?
sed -n '/^Verification:/,$p' "$WORK/v4.txt"
check_exit "$code" 1 "verify (same exception, different frame)"
check "$WORK/v4.txt" "Verification: PARTIAL"
check "$WORK/v4.txt" "the run failed, and only part of the reported signature matched"
check "$WORK/v4.txt" "  expected (from the issue): ValueError: invalid literal for int() with base 10: '-'; test_negative_operand"
check "$WORK/v4.txt" "  observed (from the run):   ValueError: invalid literal for int() with base 10: '-'; tests/test_evaluate.py::test_negative_operand"
check "$WORK/v4.txt" "exception: match"
check "$WORK/v4.txt" "message:   exact"
check "$WORK/v4.txt" "frames:    mismatch"
check "$WORK/v4.txt" "tests:     match (test_negative_operand)"
check "$WORK/v4.txt" "note: the issue's traceback raises in tokenize (src/tinycalc/evaluate.py), the run raised in evaluate (src/tinycalc/evaluate.py)"

# A Go bug report. The reproduction pipeline itself is Python and Node only,
# so this part deliberately shows BOTH halves of the truth: the Go panic is
# read and its frames are mapped onto real files in the repository, while the
# language and test command are honestly reported as not detected.
cp -R "$ROOT/examples/goshop" "$WORK/goshop"
git -C "$WORK/goshop" init --quiet
git -C "$WORK/goshop" add --all
git -C "$WORK/goshop" -c user.name=fixture -c user.email=fixture@example.invalid \
    commit --quiet --message "goshop demo fixture"

echo "== issue2repro inspect: a Go panic, read from the issue =="
"${I2R[@]}" inspect "https://github.com/example/goshop/issues/1" \
    --issue-file "$ROOT/examples/goshop-issue-1.json" \
    --clone-url "file://$WORK/goshop" | tee "$WORK/goshop.txt"

check "$WORK/goshop.txt" "  stack trace: go, 3 frame(s) (panic: runtime error: integer divide by zero)"
check "$WORK/goshop.txt" "[25/25] stack trace: go stack trace maps to existing file(s): pricing/pricing.go, cmd/shop/main.go"
check "$WORK/goshop.txt" "Language: unknown"
check "$WORK/goshop.txt" "Reproduction confidence: 60% inferred"

# A Java bug report, whose trace carries a `Caused by:` chain. The two checked
# trace lines are the point, because the rethrown IllegalStateException is the
# handler and the NullPointerException under it is the bug. Unlike the Go part
# above, this repository IS detected: its pom.xml gives a language, a test
# command, and a build command, which is what takes it to 100%.
cp -R "$ROOT/examples/javashop" "$WORK/javashop"
git -C "$WORK/javashop" init --quiet
git -C "$WORK/javashop" add --all
git -C "$WORK/javashop" -c user.name=fixture -c user.email=fixture@example.invalid \
    commit --quiet --message "javashop demo fixture"

echo "== issue2repro inspect: a JVM exception chain, read from the issue =="
"${I2R[@]}" inspect "https://github.com/example/javashop/issues/1" \
    --issue-file "$ROOT/examples/javashop-issue-1.json" \
    --clone-url "file://$WORK/javashop" | tee "$WORK/javashop.txt"

check "$WORK/javashop.txt" "  stack trace: jvm, 2 frame(s) (java.lang.IllegalStateException: checkout failed)"
check "$WORK/javashop.txt" "  stack trace: jvm, 2 frame(s) (java.lang.NullPointerException: Cannot invoke"
check "$WORK/javashop.txt" "[25/25] stack trace: jvm stack trace maps to existing file(s): src/main/java/com/example/shop/Service.java, src/main/java/com/example/shop/Pricing.java"
check "$WORK/javashop.txt" "Language: jvm (pom.xml)"
check "$WORK/javashop.txt" "Test command: mvn -B test"
check "$WORK/javashop.txt" "Build command: mvn -B -DskipTests package"
check "$WORK/javashop.txt" "Reproduction confidence: 100% inferred"

# The same repository, reported the way most JVM projects actually fail: the
# reporter pasted a `mvn test` build log rather than a hand-run java command.
# The point of this part is the "failing tests named" line. Surefire prints
# each failure twice, once per test and once in the end-of-run summary, and
# the class-level "Tests run: 3, ... <<< FAILURE! -- in ...ShippingTest" line
# has the same ending as a test line without being one. All three names, and
# only the three, come back.
echo "== issue2repro inspect: a Maven Surefire build log, read from the issue =="
"${I2R[@]}" inspect "https://github.com/example/javashop/issues/2" \
    --issue-file "$ROOT/examples/javashop-issue-2.json" \
    --clone-url "file://$WORK/javashop" | tee "$WORK/javashop-mvn.txt"

check "$WORK/javashop-mvn.txt" "  failing tests named: PricingTest::unknownCouponIsIgnored, ShippingTest::flatRateUnderThreshold, ShippingTest::freeOverFiftyDollars"
check "$WORK/javashop-mvn.txt" "  stack trace: jvm, 2 frame(s) (java.lang.NullPointerException: Cannot invoke"
check "$WORK/javashop-mvn.txt" "[25/25] stack trace: jvm stack trace maps to existing file(s): src/main/java/com/example/shop/Pricing.java, src/test/java/com/example/shop/PricingTest.java"

# The workspace that same report now generates. The Dockerfile carries a base
# image with Maven on it, and the fallback test command is scoped to the test
# class the trace implicates, with the guard that keeps a filter matching
# nothing from failing the build and reading as a reproduction.
echo
echo "== issue2repro build: the JVM workspace =="
"${I2R[@]}" build "https://github.com/example/javashop/issues/2" \
    --issue-file "$ROOT/examples/javashop-issue-2.json" \
    --clone-url "file://$WORK/javashop" \
    --output "$WORK/javarepro" >/dev/null

echo "-- Dockerfile"
cat "$WORK/javarepro/Dockerfile"
check "$WORK/javarepro/Dockerfile" "FROM maven:3.9-eclipse-temurin-21"

echo "-- scoped fallback test command"
"${I2R[@]}" build "https://github.com/example/javashop/issues/3" \
    --issue-file "$ROOT/examples/javashop-issue-3.json" \
    --clone-url "file://$WORK/javashop" \
    --output "$WORK/javarepro-scoped" >/dev/null
tail -n 1 "$WORK/javarepro-scoped/reproduce.sh"
check "$WORK/javarepro-scoped/reproduce.sh" \
    "mvn -B test -Dtest=PricingTest -Dsurefire.failIfNoSpecifiedTests=false"

# Everything above this line is analysis and generation, which need no JVM
# toolchain at all. This part EXECUTES the JVM workspace: `mvn test` really
# runs, Surefire really fails, and the failure it prints is compared against
# the one the issue reports. On the container path Maven comes from the
# image, so the host needs nothing but Docker. On the host path it needs a
# real Maven, which not every machine has, so that case is skipped out loud
# rather than quietly asserting less than the line above it implies.
echo
if [ "${DEMO_VERIFY_DOCKER:-}" = "1" ] || command -v mvn >/dev/null 2>&1; then
    echo "== issue2repro verify: the JVM workspace, actually run =="
    code=0
    "${I2R[@]}" verify "https://github.com/example/javashop/issues/2" \
        --issue-file "$ROOT/examples/javashop-issue-2.json" \
        --clone-url "file://$WORK/javashop" \
        --output "$WORK/javaverify" \
        $HOST_FLAG >"$WORK/vjvm.txt" 2>&1 || code=$?
    sed -n '/^Verification:/,$p' "$WORK/vjvm.txt"
    check_exit "$code" 0 "verify (JVM reproduced)"
    check "$WORK/vjvm.txt" "Verification: REPRODUCED ($(where_clause example-javashop-2))"
    check "$WORK/vjvm.txt" "exception: match"
    check "$WORK/vjvm.txt" "message:   exact"
    check "$WORK/vjvm.txt" "frames:    match (applyCoupon in Pricing.java)"
    # Surefire's class order is not something this tool controls, so the
    # names are checked individually rather than as one ordered line.
    check "$WORK/vjvm.txt" "tests:     match ("
    check "$WORK/vjvm.txt" "PricingTest::unknownCouponIsIgnored"
    check "$WORK/vjvm.txt" "ShippingTest::freeOverFiftyDollars"
    check "$WORK/vjvm.txt" "ShippingTest::flatRateUnderThreshold"
else
    echo "== issue2repro verify: the JVM workspace [SKIPPED] =="
    echo "   No mvn on PATH and DEMO_VERIFY_DOCKER is not set, so the JVM"
    echo "   reproduction was generated above but not executed. Install Maven,"
    echo "   or set DEMO_VERIFY_DOCKER=1 to run it in a container instead."
fi

# The other JVM build tool. examples/gradleshop commits NO Gradle wrapper, so
# `gradle` comes from PATH on the host and from the gradle:8-jdk21 base image
# in the container. That is deliberate: a wrapper only works if its JAR is
# committed, and this repository does not vendor binaries.
echo
echo "== issue2repro inspect: a Gradle repository =="
cp -R "$ROOT/examples/gradleshop" "$WORK/gradleshop"
git -C "$WORK/gradleshop" init --quiet
git -C "$WORK/gradleshop" add --all
git -C "$WORK/gradleshop" -c user.name=fixture -c user.email=fixture@example.invalid \
    commit --quiet --message "gradleshop demo fixture"

"${I2R[@]}" inspect "https://github.com/example/gradleshop/issues/1" \
    --issue-file "$ROOT/examples/gradleshop-issue-1.json" \
    --clone-url "file://$WORK/gradleshop" | tee "$WORK/gradleshop.txt"

check "$WORK/gradleshop.txt" "Language: jvm (build.gradle, settings.gradle)"
check "$WORK/gradleshop.txt" "Test command: gradle --no-daemon test"
check "$WORK/gradleshop.txt" "Build command: gradle --no-daemon build -x test"
check "$WORK/gradleshop.txt" "  failing tests named: PricingTest::unknownCouponIsIgnored"
# Gradle's default output carries no stack trace, so there is nothing to map
# to a file and the confidence is honestly lower than the Maven example's.
check "$WORK/gradleshop.txt" "  stack traces: none"
check "$WORK/gradleshop.txt" "Reproduction confidence: 75% inferred"

echo
echo "== issue2repro build: the Gradle workspace =="
"${I2R[@]}" build "https://github.com/example/gradleshop/issues/1" \
    --issue-file "$ROOT/examples/gradleshop-issue-1.json" \
    --clone-url "file://$WORK/gradleshop" \
    --output "$WORK/gradlerepro" >/dev/null
echo "-- Dockerfile"
cat "$WORK/gradlerepro/Dockerfile"
check "$WORK/gradlerepro/Dockerfile" "FROM gradle:8-jdk21"

# This EXECUTES the Gradle workspace, the half that was generated but never
# run until now. Maven and Gradle are both real end to end from here.
echo
if [ "${DEMO_VERIFY_DOCKER:-}" = "1" ] || command -v gradle >/dev/null 2>&1; then
    echo "== issue2repro verify: the Gradle workspace, actually run =="
    code=0
    "${I2R[@]}" verify "https://github.com/example/gradleshop/issues/1" \
        --issue-file "$ROOT/examples/gradleshop-issue-1.json" \
        --clone-url "file://$WORK/gradleshop" \
        --output "$WORK/gradleverify" \
        $HOST_FLAG >"$WORK/vgradle.txt" 2>&1 || code=$?
    sed -n '/^Verification:/,$p' "$WORK/vgradle.txt"
    check_exit "$code" 0 "verify (Gradle reproduced)"
    check "$WORK/vgradle.txt" "Verification: REPRODUCED ($(where_clause example-gradleshop-1))"
    check "$WORK/vgradle.txt" "exception: match"
    check "$WORK/vgradle.txt" "tests:     match (PricingTest::unknownCouponIsIgnored)"
    # Gradle prints no stack trace by default, so there is no frame to compare
    # and `verify` prints no frames line at all rather than guessing. The
    # README says so, and this is what holds it to that.
    if grep -q "  frames:" "$WORK/vgradle.txt"; then
        echo "DEMO CHECK FAILED: expected NO frames line in vgradle.txt" >&2
        FAILURES=$((FAILURES + 1))
    fi
else
    echo "== issue2repro verify: the Gradle workspace [SKIPPED] =="
    echo "   No gradle on PATH and DEMO_VERIFY_DOCKER is not set, so the"
    echo "   Gradle reproduction was generated above but not executed."
    echo "   Install Gradle, or set DEMO_VERIFY_DOCKER=1 to run it in a"
    echo "   container instead."
fi

echo
if [ "$FAILURES" -ne 0 ]; then
    echo "demo.sh: $FAILURES check(s) failed. The README and the tool disagree." >&2
    exit 1
fi
echo "demo.sh: all checks passed."
