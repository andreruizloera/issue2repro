"""Run a reproduction workspace and decide whether the reported bug reproduced.

`run` executes reproduce.sh and reports its exit code, which cannot tell a
reproduced bug from a failed `pip install`: both exit nonzero. This module
separates them two ways. Step markers in reproduce.sh say which command was
running when the script died, so a failure during setup is reported as an
environment failure and never as a reproduction. Then the failure the run
produced is compared against the one the issue describes, so a run that
fails for an unrelated reason is reported as a different failure.

Verdicts, and the exit code each maps to:

    reproduced           0   the reported failure was observed
    partial              1   some of the signature matched, some did not
    different-failure    1   the reproduction step failed, but not this way
    not-reproduced       1   the reproduction step ran and exited 0
    environment-failure  2   the script died during setup; nothing was tested
    timed-out            2   the run was killed before it finished
    unknown              2   the issue carries no signature to check against

Two of the three exit codes mean "not confirmed" and one means "could not
tell". A verifier that answers "could not tell" as though it were "no" is
the same rubber stamp as one that answers it as "yes".
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from issue2repro.github import Issue2ReproError
from issue2repro.models import (
    FailureSignature,
    Signals,
    SignatureMatch,
    StackFrame,
    StackTrace,
    Step,
    Verification,
)
from issue2repro.signature import (
    compare_signatures,
    expected_signature,
    observed_signature,
    verdict_from_match,
)
from issue2repro.workspace import STEP_MARKER_PREFIX

_MARKER = re.compile(rf"^{re.escape(STEP_MARKER_PREFIX)}(?P<index>\d+):(?P<kind>setup|repro)\s*$")

DEFAULT_TIMEOUT = 900

EXIT_CODES = {
    "reproduced": 0,
    "partial": 1,
    "different-failure": 1,
    "not-reproduced": 1,
    "environment-failure": 2,
    "timed-out": 2,
    "unknown": 2,
}


@dataclass
class RunOutcome:
    """What happened when the workspace ran."""

    exit_code: int
    output: str
    last_step: Step | None
    timed_out: bool
    where: str


def _stream(
    command: list[str],
    cwd: Path | None,
    env: dict[str, str] | None,
    timeout: int,
    echo: Callable[[str], None] | None,
) -> tuple[int, str, bool]:
    """Run a command, echoing its output live while capturing all of it.

    Step markers are captured but never echoed: they are addressed to
    verify, not to the person watching the run.
    """
    try:
        proc = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        raise Issue2ReproError(f"{command[0]} is not available: {exc}") from exc

    captured: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            captured.append(line)
            if echo is not None and not _MARKER.match(line.strip()):
                echo(line.rstrip("\n"))

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    timed_out = False
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        proc.kill()
        code = proc.wait()
    reader.join(timeout=10)
    return code, "".join(captured), timed_out


def _last_step(output: str, steps: list[Step]) -> Step | None:
    """The step that was running when the script stopped.

    reproduce.sh runs under `set -e`, so the last marker printed is the
    command that failed. A run with no markers at all (an older workspace,
    or a script that never started) yields None, which verify reports as
    not knowing rather than guessing.
    """
    by_index = {step.index: step for step in steps}
    last: Step | None = None
    for line in output.splitlines():
        marker = _MARKER.match(line.strip())
        if not marker:
            continue
        index = int(marker["index"])
        last = by_index.get(index) or Step(index=index, kind=marker["kind"], command="")
    return last


def strip_markers(output: str) -> str:
    """Output as a human should read it, with the step markers removed."""
    return "\n".join(line for line in output.splitlines() if not _MARKER.match(line.strip()))


def load_workspace(out_dir: Path) -> tuple[dict, list[Step]]:
    """Read metadata.json and the recorded step plan from a built workspace."""
    metadata_path = out_dir / "metadata.json"
    if not metadata_path.exists():
        raise Issue2ReproError(
            f"{metadata_path} not found. Build the workspace first: issue2repro build URL"
        )
    try:
        metadata = json.loads(metadata_path.read_text())
    except json.JSONDecodeError as exc:
        raise Issue2ReproError(f"{metadata_path} is not valid JSON: {exc}") from exc
    steps = [
        Step(index=int(s["index"]), kind=str(s["kind"]), command=str(s["command"]))
        for s in metadata.get("reproduce_steps", [])
    ]
    return metadata, steps


def docker_available() -> bool:
    return shutil.which("docker") is not None


def image_tag(metadata: dict) -> str:
    """A per-issue image tag, sanitized to what Docker accepts."""
    issue = metadata.get("issue", {})
    raw = f"{issue.get('owner', 'unknown')}-{issue.get('repo', 'repo')}-{issue.get('number', 0)}"
    slug = re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-.") or "workspace"
    return f"issue2repro-verify:{slug}"


def run_in_docker(
    out_dir: Path,
    steps: list[Step],
    tag: str,
    timeout: int,
    echo: Callable[[str], None] | None,
) -> RunOutcome:
    """Build the workspace Dockerfile and run reproduce.sh inside it."""
    if not docker_available():
        raise Issue2ReproError(
            "docker was not found on PATH, so the reproduction cannot be run in a container.\n"
            "Install Docker, or pass --no-docker to run reproduce.sh on this machine "
            "(which executes repository code directly).",
            exit_code=2,
        )
    if echo is not None:
        echo(f"Building image {tag} from {out_dir / 'Dockerfile'} ...")
    build_code, build_output, build_timed_out = _stream(
        ["docker", "build", "--tag", tag, str(out_dir)], None, None, timeout, echo
    )
    if build_timed_out:
        return RunOutcome(
            exit_code=build_code,
            output=build_output,
            last_step=None,
            timed_out=True,
            where="in Docker (image build)",
        )
    if build_code != 0:
        return RunOutcome(
            exit_code=build_code,
            output=build_output,
            last_step=Step(index=0, kind="setup", command="docker build"),
            timed_out=False,
            where="in Docker (image build)",
        )
    code, output, timed_out = _stream(
        ["docker", "run", "--rm", "--env", "ISSUE2REPRO_TRACE=1", tag],
        None,
        None,
        timeout,
        echo,
    )
    return RunOutcome(
        exit_code=code,
        output=output,
        last_step=_last_step(output, steps),
        timed_out=timed_out,
        where=f"in Docker ({tag})",
    )


def run_on_host(
    out_dir: Path,
    steps: list[Step],
    timeout: int,
    echo: Callable[[str], None] | None,
    environ: dict[str, str] | None = None,
) -> RunOutcome:
    """Run reproduce.sh directly on this machine."""
    script = out_dir / "reproduce.sh"
    if not script.exists():
        raise Issue2ReproError(f"{script} not found. Build the workspace first.")
    env = dict(environ if environ is not None else os.environ)
    env["ISSUE2REPRO_TRACE"] = "1"
    code, output, timed_out = _stream(["bash", str(script)], None, env, timeout, echo)
    return RunOutcome(
        exit_code=code,
        output=output,
        last_step=_last_step(output, steps),
        timed_out=timed_out,
        where="on this machine",
    )


def decide(expected: FailureSignature, outcome: RunOutcome) -> Verification:
    """Turn a run and an expected signature into a verdict.

    The order of the checks is the design. A setup failure and a timeout are
    decided before any signature is compared, because in both cases the
    reproduction command did not run to completion and whatever is in the
    output is not evidence about the bug.
    """
    # The issue's own Gradle block is named first and the rest of the tests it
    # points at follow, so a run that failed several tests reports the one the
    # issue was about rather than whichever Gradle happened to print first.
    prefer = [expected.gradle_test, *expected.tests] if expected.gradle_test else expected.tests
    observed = observed_signature(
        outcome.output,
        expect_type=expected.exception_type,
        expect_tests=prefer,
    )

    if outcome.timed_out:
        return Verification(
            verdict="timed-out",
            where=outcome.where,
            exit_code=outcome.exit_code,
            expected=expected,
            observed=observed,
            match=SignatureMatch(),
            failed_step=outcome.last_step,
            reason="the run was killed before it finished, so nothing was observed",
        )

    failed_step = outcome.last_step if outcome.exit_code != 0 else None

    if failed_step is not None and failed_step.kind == "setup":
        where = f"step {failed_step.index}" + (
            f" ({failed_step.command})" if failed_step.command else ""
        )
        return Verification(
            verdict="environment-failure",
            where=outcome.where,
            exit_code=outcome.exit_code,
            expected=expected,
            observed=observed,
            match=SignatureMatch(),
            failed_step=failed_step,
            reason=(
                f"the script exited during setup, at {where}. "
                "The reproduction never ran, so this says nothing about the bug."
            ),
        )

    if expected.is_empty:
        return Verification(
            verdict="unknown",
            where=outcome.where,
            exit_code=outcome.exit_code,
            expected=expected,
            observed=observed,
            match=SignatureMatch(notes=["the issue describes no checkable failure"]),
            failed_step=failed_step,
            reason=(
                "the issue carries no exception and no test name, so there is nothing "
                "to compare this run against"
            ),
        )

    if outcome.exit_code == 0:
        return Verification(
            verdict="not-reproduced",
            where=outcome.where,
            exit_code=0,
            expected=expected,
            observed=observed,
            match=SignatureMatch(),
            failed_step=None,
            reason="the reproduction ran to completion and exited 0",
        )

    match = compare_signatures(expected, observed)
    verdict = verdict_from_match(match)
    if verdict == "unknown":
        reason = (
            "the run failed, but nothing in its output could be compared "
            "against what the issue describes"
        )
    elif verdict == "reproduced":
        reason = "the run failed the way the issue describes"
    elif verdict == "partial":
        reason = "the run failed, and only part of the reported signature matched"
    else:
        reason = "the run failed for a different reason than the issue reports"
    return Verification(
        verdict=verdict,
        where=outcome.where,
        exit_code=outcome.exit_code,
        expected=expected,
        observed=observed,
        match=match,
        failed_step=failed_step,
        reason=reason,
    )


def verify_workspace(
    out_dir: Path,
    use_docker: bool = True,
    timeout: int = DEFAULT_TIMEOUT,
    echo: Callable[[str], None] | None = None,
    environ: dict[str, str] | None = None,
) -> Verification:
    """Run a built workspace and compare what happened against the issue."""
    metadata, steps = load_workspace(out_dir)
    expected = expected_signature(
        _signals_from_metadata(metadata), _issue_text_from_workspace(out_dir)
    )
    if use_docker:
        outcome = run_in_docker(out_dir, steps, image_tag(metadata), timeout, echo)
    else:
        outcome = run_on_host(out_dir, steps, timeout, echo, environ=environ)

    (out_dir / "verify.log").write_text(strip_markers(outcome.output) + "\n")

    verification = decide(expected, outcome)
    metadata["verification"] = verification.to_metadata()
    (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return verification


def _signals_from_metadata(metadata: dict) -> Signals:
    """Rebuild the Signals recorded at build time."""
    raw = metadata.get("signals", {}) or {}
    traces = []
    for trace in raw.get("traces", []) or []:
        frames = [
            StackFrame(
                path=str(f.get("path", "")),
                line=int(f.get("line", 0)),
                symbol=f.get("symbol"),
            )
            for f in trace.get("frames", []) or []
        ]
        traces.append(
            StackTrace(
                language=str(trace.get("language", "")),
                frames=frames,
                error=trace.get("error"),
            )
        )
    return Signals(
        traces=traces,
        commands=list(raw.get("commands", []) or []),
        filenames=list(raw.get("filenames", []) or []),
        code_refs=list(raw.get("code_refs", []) or []),
    )


def _issue_text_from_workspace(out_dir: Path) -> str:
    issue_md = out_dir / "issue.md"
    return issue_md.read_text() if issue_md.exists() else ""


def render_verification(verification: Verification) -> list[str]:
    """The human-readable verdict block."""
    lines = [
        f"Verification: {verification.verdict.upper()} "
        f"(observed by running reproduce.sh {verification.where})",
        f"  {verification.reason}",
    ]
    expected, observed, match = verification.expected, verification.observed, verification.match
    lines.append(f"  expected (from the issue): {expected.describe()}")
    lines.append(f"  observed (from the run):   {observed.describe()}")
    if match.exception != "unknown":
        lines.append(f"  exception: {match.exception}")
    if match.message != "unknown":
        lines.append(f"  message:   {match.message}")
    if match.frames == "match":
        lines.append(f"  frames:    match ({match.matched_frame})")
    elif match.frames == "mismatch":
        lines.append("  frames:    mismatch")
    if match.tests == "match":
        lines.append(f"  tests:     match ({', '.join(match.matched_tests)})")
    elif match.tests == "mismatch":
        lines.append("  tests:     mismatch")
    for note in match.notes:
        lines.append(f"  note: {note}")
    return lines


def echo_stdout(line: str) -> None:
    print(line, flush=True)
