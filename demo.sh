#!/usr/bin/env bash
# Offline demo: run the full issue2repro pipeline against the committed
# fixture project in examples/tinycalc and the committed bug report in
# examples/tinycalc-issue-1.json. No network access is needed: the issue
# is read from a local JSON file and the repository is cloned over file://.
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

# Turn the committed fixture into a standalone git repo so it can be
# shallow-cloned exactly like a real project.
cp -R "$ROOT/examples/tinycalc" "$WORK/tinycalc"
git -C "$WORK/tinycalc" init --quiet
git -C "$WORK/tinycalc" add --all
git -C "$WORK/tinycalc" -c user.name=fixture -c user.email=fixture@example.invalid \
    commit --quiet --message "tinycalc demo fixture"

ISSUE_URL="https://github.com/example/tinycalc/issues/1"

echo "== issue2repro build =="
"${I2R[@]}" build "$ISSUE_URL" \
    --issue-file "$ROOT/examples/tinycalc-issue-1.json" \
    --clone-url "file://$WORK/tinycalc" \
    --output "$WORK/repro"

echo
echo "== workspace tree =="
(cd "$WORK/repro" && find . -not -path "./source/.git" -not -path "./source/.git/*" | sort)

echo
echo "== generated reproduce.sh =="
cat "$WORK/repro/reproduce.sh"

echo
echo "== issue2repro run =="
"${I2R[@]}" run "$ISSUE_URL" \
    --issue-file "$ROOT/examples/tinycalc-issue-1.json" \
    --clone-url "file://$WORK/tinycalc" \
    --output "$WORK/repro"
