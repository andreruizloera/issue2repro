"""Tests for `issue2repro verify`.

The comparison logic is exercised through decide() with hand-built run
outcomes, and the execution path through run_on_host() against workspaces
written in tmp_path. Nothing here needs Docker or the network.
"""

import json
from pathlib import Path

import pytest

from issue2repro.github import Issue2ReproError
from issue2repro.models import FailureSignature, StackFrame, Step
from issue2repro.verify import (
    EXIT_CODES,
    RunOutcome,
    decide,
    image_tag,
    load_workspace,
    render_verification,
    run_in_docker,
    run_on_host,
    strip_markers,
    verify_workspace,
)
from issue2repro.workspace import STEP_MARKER_PREFIX

SETUP = Step(index=1, kind="setup", command="pip install -r requirements-dev.txt")
REPRO = Step(index=2, kind="repro", command="python -m pytest -x")

PYTEST_FAILURE = (
    "FAILED tests/test_evaluate.py::test_negative_operand - ValueError: invalid li...\n"
    "E   ValueError: invalid literal for int() with base 10: '-'\n"
)

EXPECTED = FailureSignature(
    exception_type="ValueError",
    exception_message="invalid literal for int() with base 10: '-'",
    tests=["test_negative_operand"],
)


def outcome(exit_code=1, output="", last_step=REPRO, timed_out=False):
    return RunOutcome(
        exit_code=exit_code,
        output=output,
        last_step=last_step,
        timed_out=timed_out,
        where="on this machine",
    )


def write_workspace_dir(tmp_path: Path, script_body: str, steps: list[Step], signals=None) -> Path:
    """A minimal built workspace: metadata.json plus a reproduce.sh with markers."""
    out = tmp_path / "repro"
    out.mkdir()
    metadata = {
        "issue": {"owner": "acme", "repo": "widget", "number": 7},
        "signals": signals
        or {
            "traces": [
                {
                    "language": "python",
                    "frames": [
                        {"path": "tests/test_evaluate.py", "line": 13, "symbol": "test_negative"}
                    ],
                    "error": "ValueError: boom",
                }
            ],
            "commands": [],
            "filenames": [],
            "code_refs": [],
        },
        "reproduce_steps": [
            {"index": s.index, "kind": s.kind, "command": s.command} for s in steps
        ],
    }
    (out / "metadata.json").write_text(json.dumps(metadata))
    marker_lines = "\n".join(
        f"printf '{STEP_MARKER_PREFIX}%s:%s\\n' {s.index} {s.kind}" for s in steps
    )
    (out / "reproduce.sh").write_text(f"set -euo pipefail\n{marker_lines}\n{script_body}\n")
    return out


class TestDecide:
    def test_reproduced(self):
        result = decide(EXPECTED, outcome(output=PYTEST_FAILURE))
        assert result.verdict == "reproduced"
        assert EXIT_CODES[result.verdict] == 0

    def test_clean_run_is_not_reproduced(self):
        result = decide(EXPECTED, outcome(exit_code=0, output="3 passed", last_step=REPRO))
        assert result.verdict == "not-reproduced"
        assert EXIT_CODES[result.verdict] == 1

    def test_different_failure(self):
        output = "E   ZeroDivisionError: integer division or modulo by zero\n"
        result = decide(EXPECTED, outcome(output=output))
        assert result.verdict == "different-failure"
        assert EXIT_CODES[result.verdict] == 1

    def test_setup_failure_never_reads_as_reproduced(self):
        """The decisive test: the reported failure is in the output, but the
        script died in setup, so nothing was actually exercised."""
        output = "ERROR: could not install\n" + PYTEST_FAILURE
        result = decide(EXPECTED, outcome(output=output, last_step=SETUP))
        assert result.verdict == "environment-failure"
        assert EXIT_CODES[result.verdict] == 2
        assert "pip install -r requirements-dev.txt" in result.reason
        assert result.failed_step == SETUP

    def test_timeout_outranks_everything(self):
        result = decide(EXPECTED, outcome(output=PYTEST_FAILURE, timed_out=True))
        assert result.verdict == "timed-out"
        assert EXIT_CODES[result.verdict] == 2

    def test_no_expected_signature_is_unknown_not_reproduced(self):
        result = decide(FailureSignature(), outcome(output=PYTEST_FAILURE))
        assert result.verdict == "unknown"
        assert EXIT_CODES[result.verdict] == 2
        assert "nothing" in result.reason

    def test_unrecognized_failure_is_unknown(self):
        result = decide(EXPECTED, outcome(output="make: *** [all] Error 1\n"))
        assert result.verdict == "unknown"

    def test_a_clean_run_is_still_judged_when_the_last_step_is_setup(self):
        """Exit 0 means the whole script ran, so the last marker is not a failure."""
        result = decide(EXPECTED, outcome(exit_code=0, output="ok", last_step=SETUP))
        assert result.verdict == "not-reproduced"

    def test_observed_signature_is_recorded_even_when_unusable(self):
        result = decide(EXPECTED, outcome(output=PYTEST_FAILURE, last_step=SETUP))
        assert result.observed.exception_type == "ValueError"
        assert result.verdict == "environment-failure"

    def test_a_gradle_run_answers_about_the_test_the_issue_named(self, jvm_gradle_full_exception):
        """decide() is the wiring, and it is what the CLI actually calls.

        The signature module can align the two sides only if the run side is
        told which test the issue was about. This drives the whole path: an
        issue quoting one failing test out of the four this build produced,
        against the build's own output.
        """
        run = jvm_gradle_full_exception
        expected = FailureSignature(
            exception_type="org.opentest4j.AssertionFailedError",
            exception_message="expected: <0> but was: <599>",
            tests=["ShippingTest::freeOverFiftyDollars"],
            gradle_test="ShippingTest::freeOverFiftyDollars",
        )
        result = decide(expected, outcome(output=run))
        assert result.observed.gradle_test == "ShippingTest::freeOverFiftyDollars"
        assert result.observed.exception_type == "org.opentest4j.AssertionFailedError"
        assert result.match.exception == "match"
        assert result.verdict == "reproduced"

        # The negative control: the same run, with the issue naming nothing to
        # align on, lands on Gradle's first block instead and disagrees.
        blind = decide(
            FailureSignature(exception_type="org.opentest4j.AssertionFailedError"),
            outcome(output=run),
        )
        assert blind.observed.exception_type == "java.lang.NullPointerException"
        assert blind.match.exception == "mismatch"


class TestMarkers:
    def test_last_marker_wins(self, tmp_path):
        out = write_workspace_dir(tmp_path, "exit 3", [SETUP, REPRO])
        result = run_on_host(out, [SETUP, REPRO], timeout=60, echo=None)
        assert result.exit_code == 3
        assert result.last_step == REPRO

    def test_a_run_that_stops_early_reports_the_setup_step(self, tmp_path):
        out = tmp_path / "repro"
        out.mkdir()
        (out / "reproduce.sh").write_text(
            f"set -e\nprintf '{STEP_MARKER_PREFIX}%s:%s\\n' 1 setup\nexit 4\n"
        )
        result = run_on_host(out, [SETUP, REPRO], timeout=60, echo=None)
        assert result.last_step == SETUP
        assert result.exit_code == 4

    def test_markers_are_not_echoed_to_the_reader(self, tmp_path):
        out = write_workspace_dir(tmp_path, "echo hello", [REPRO])
        seen: list[str] = []
        run_on_host(out, [REPRO], timeout=60, echo=seen.append)
        assert "hello" in seen
        assert not any(STEP_MARKER_PREFIX in line for line in seen)

    def test_a_workspace_with_no_markers_yields_no_step(self, tmp_path):
        out = tmp_path / "repro"
        out.mkdir()
        (out / "reproduce.sh").write_text("exit 1\n")
        assert run_on_host(out, [], timeout=60, echo=None).last_step is None

    def test_strip_markers_leaves_real_output_alone(self):
        text = f"one\n{STEP_MARKER_PREFIX}1:setup\ntwo\n"
        assert strip_markers(text) == "one\ntwo"

    def test_the_script_is_silent_without_the_trace_variable(self, tmp_path):
        out = write_workspace_dir(tmp_path, "echo hello", [REPRO])
        seen: list[str] = []
        run_on_host(out, [REPRO], timeout=60, echo=seen.append, environ={"PATH": "/usr/bin:/bin"})
        assert "hello" in seen


class TestTimeout:
    def test_a_slow_run_is_killed_and_reported(self, tmp_path):
        out = write_workspace_dir(tmp_path, "sleep 30", [REPRO])
        result = run_on_host(out, [REPRO], timeout=1, echo=None)
        assert result.timed_out is True


class TestWorkspaceLoading:
    def test_missing_metadata_is_a_clean_error(self, tmp_path):
        with pytest.raises(Issue2ReproError, match="Build the workspace first"):
            load_workspace(tmp_path)

    def test_invalid_metadata_is_a_clean_error(self, tmp_path):
        (tmp_path / "metadata.json").write_text("{not json")
        with pytest.raises(Issue2ReproError, match="not valid JSON"):
            load_workspace(tmp_path)

    def test_steps_are_read_back(self, tmp_path):
        out = write_workspace_dir(tmp_path, "true", [SETUP, REPRO])
        _, steps = load_workspace(out)
        assert [s.kind for s in steps] == ["setup", "repro"]
        assert steps[0].command == SETUP.command

    def test_missing_script_is_a_clean_error(self, tmp_path):
        with pytest.raises(Issue2ReproError, match="not found"):
            run_on_host(tmp_path, [], timeout=5, echo=None)


class TestImageTag:
    def test_tag_is_per_issue_and_sanitized(self):
        # underscore, dot, and hyphen are legal in a Docker tag; case is not
        tag = image_tag({"issue": {"owner": "Acme_Corp", "repo": "My.Widget", "number": 7}})
        assert tag == "issue2repro-verify:acme_corp-my.widget-7"

    def test_illegal_characters_are_replaced(self):
        tag = image_tag({"issue": {"owner": "a b/c", "repo": "r", "number": 1}})
        assert tag == "issue2repro-verify:a-b-c-r-1"

    def test_missing_issue_data_still_produces_a_valid_tag(self):
        assert image_tag({}) == "issue2repro-verify:unknown-repo-0"


class TestDockerAvailability:
    def test_missing_docker_is_a_could_not_tell_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr("issue2repro.verify.shutil.which", lambda _: None)
        with pytest.raises(Issue2ReproError) as excinfo:
            run_in_docker(tmp_path, [], "tag", 60, None)
        assert excinfo.value.exit_code == 2
        assert "--no-docker" in str(excinfo.value)


class TestVerifyWorkspace:
    def test_writes_the_log_and_the_verdict_into_metadata(self, tmp_path):
        out = write_workspace_dir(
            tmp_path,
            "echo 'E   ValueError: boom'; exit 1",
            [REPRO],
        )
        verification = verify_workspace(out, use_docker=False, timeout=60, echo=None)
        assert verification.verdict == "reproduced"
        assert "ValueError: boom" in (out / "verify.log").read_text()
        assert STEP_MARKER_PREFIX not in (out / "verify.log").read_text()
        meta = json.loads((out / "metadata.json").read_text())
        assert meta["verification"]["verdict"] == "reproduced"
        assert meta["verification"]["match"]["exception"] == "match"

    def test_expected_signature_comes_from_the_recorded_signals(self, tmp_path):
        out = write_workspace_dir(tmp_path, "echo 'E   KeyError: nope'; exit 1", [REPRO])
        verification = verify_workspace(out, use_docker=False, timeout=60, echo=None)
        assert verification.expected.exception_type == "ValueError"
        assert verification.verdict == "different-failure"

    def test_a_workspace_with_no_signals_reports_unknown(self, tmp_path):
        empty = {"traces": [], "commands": [], "filenames": [], "code_refs": []}
        out = write_workspace_dir(tmp_path, "exit 1", [REPRO], signals=empty)
        verification = verify_workspace(out, use_docker=False, timeout=60, echo=None)
        assert verification.verdict == "unknown"


class TestRendering:
    def test_verdict_block_names_both_signatures(self):
        verification = decide(EXPECTED, outcome(output=PYTEST_FAILURE))
        text = "\n".join(render_verification(verification))
        assert "REPRODUCED" in text
        assert "expected (from the issue)" in text
        assert "observed (from the run)" in text
        assert "test_negative_operand" in text

    def test_a_frame_mismatch_names_both_frames(self):
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="invalid literal for int() with base 10: '-'",
            frames=[StackFrame(path="src/tinycalc/evaluate.py", line=12, symbol="tokenize")],
            tests=["test_negative_operand"],
        )
        output = (
            "    def evaluate(expr):\n"
            ">       value = int(tokens[i + 1])\n"
            "E       ValueError: invalid literal for int() with base 10: '-'\n"
            "\n"
            "src/tinycalc/evaluate.py:23: ValueError\n"
        )
        verification = decide(expected, outcome(output=output))
        text = "\n".join(render_verification(verification))
        assert verification.verdict == "partial"
        assert "frames:    mismatch" in text
        assert "raises in tokenize (src/tinycalc/evaluate.py)" in text
        assert "the run raised in evaluate (src/tinycalc/evaluate.py)" in text

    def test_a_mismatch_prints_its_reason(self):
        verification = decide(EXPECTED, outcome(output="E   TypeError: bad\n"))
        text = "\n".join(render_verification(verification))
        assert "note:" in text
        assert "TypeError" in text


class TestPartialSuiteExitCode:
    """The consequence of the tests component gaining a "partial" value.

    A run that reproduced one of four reported failures used to be a
    REPRODUCED verdict and exit 0, which is the answer a CI gate acts on.
    """

    SUITE = FailureSignature(
        exception_type="ValueError",
        exception_message="bad date",
        tests=["test_iso_date", "test_iso_datetime", "test_epoch_seconds", "test_rfc2822"],
    )

    ONE_OF_FOUR = (
        "=========================== short test summary info ===========================\n"
        "FAILED tests/test_parse.py::test_iso_date - ValueError: bad date\n"
        "=========================== 1 failed, 3 passed in 0.29s =======================\n"
    )

    ALL_FOUR = (
        "=========================== short test summary info ===========================\n"
        "FAILED tests/test_parse.py::test_iso_date - ValueError: bad date\n"
        "FAILED tests/test_parse.py::test_iso_datetime - ValueError: bad date\n"
        "FAILED tests/test_parse.py::test_epoch_seconds - ValueError: bad date\n"
        "FAILED tests/test_parse.py::test_rfc2822 - ValueError: bad date\n"
        "=========================== 4 failed in 0.31s =================================\n"
    )

    def test_one_of_four_exits_nonzero(self):
        result = decide(self.SUITE, outcome(output=self.ONE_OF_FOUR))
        assert result.verdict == "partial"
        assert EXIT_CODES[result.verdict] == 1

    def test_all_four_still_exits_zero(self):
        result = decide(self.SUITE, outcome(output=self.ALL_FOUR))
        assert result.verdict == "reproduced"
        assert EXIT_CODES[result.verdict] == 0

    def test_the_rendered_block_says_how_many_of_how_many(self):
        result = decide(self.SUITE, outcome(output=self.ONE_OF_FOUR))
        rendered = "\n".join(render_verification(result))
        assert "tests:     partial (1 of 4: test_iso_date)" in rendered
        assert "did not fail" in rendered
