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
# Set DEMO_VERIFY_DOCKER=1 to run the first one in a real container instead,
# which is what CI does. Every line checked below is identical either way.
# A plain string, not an array: bash 3.2 (the macOS default) treats an empty
# array as unbound under `set -u` and kills the script.
HOST_FLAG="--no-docker"
if [ "${DEMO_VERIFY_DOCKER:-}" = "1" ]; then
    HOST_FLAG=""
fi

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
check "$WORK/v1.txt" "Verification: REPRODUCED"
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

# A Java bug report, whose trace carries a `Caused by:` chain. Same shape as
# the Go part: the JVM exception is read and its frames map onto real files,
# while the language and test command stay honestly undetected. The two
# checked trace lines are the point, because the rethrown
# IllegalStateException is the handler and the NullPointerException under it
# is the bug.
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
check "$WORK/javashop.txt" "Language: unknown"
check "$WORK/javashop.txt" "Reproduction confidence: 60% inferred"

echo
if [ "$FAILURES" -ne 0 ]; then
    echo "demo.sh: $FAILURES check(s) failed. The README and the tool disagree." >&2
    exit 1
fi
echo "demo.sh: all checks passed."
