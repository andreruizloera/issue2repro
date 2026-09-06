"""End-to-end CLI tests. Fully offline: issues come from fixture JSON files
and repositories are cloned over file:// from git repos built in tmp_path."""

import json
import subprocess
from pathlib import Path

import pytest

from issue2repro.cli import main
from tests.conftest import write_issue

FIXTURES = Path(__file__).parent / "fixtures"
URL = "https://github.com/acme/widget/issues/7"


def make_git_repo(path: Path) -> str:
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "add", "--all"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--quiet",
            "--message",
            "fixture",
        ],
        check=True,
    )
    return f"file://{path}"


@pytest.fixture
def python_clone_url(python_repo: Path) -> str:
    return make_git_repo(python_repo)


class TestInspect:
    def test_prints_confidence_breakdown(self, python_clone_url, capsys):
        code = main(
            [
                "inspect",
                URL,
                "--issue-file",
                str(FIXTURES / "python_issue.json"),
                "--clone-url",
                python_clone_url,
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "Reproduction confidence: 100%" in out
        assert "[35/35] explicit repro commands" in out
        assert "Issue: acme/widget#7" in out

    def test_does_not_write_workspace(self, python_clone_url, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        main(
            [
                "inspect",
                URL,
                "--issue-file",
                str(FIXTURES / "python_issue.json"),
                "--clone-url",
                python_clone_url,
            ]
        )
        capsys.readouterr()
        assert not (tmp_path / "repro").exists()


class TestBuild:
    def test_creates_workspace(self, python_clone_url, tmp_path, capsys):
        out_dir = tmp_path / "repro"
        code = main(
            [
                "build",
                URL,
                "--issue-file",
                str(FIXTURES / "python_issue.json"),
                "--clone-url",
                python_clone_url,
                "--output",
                str(out_dir),
            ]
        )
        out = capsys.readouterr().out
        assert code == 0
        assert "Workspace written to" in out
        for name in ("source", "issue.md", "metadata.json", "reproduce.sh", "Dockerfile"):
            assert (out_dir / name).exists()
        assert (out_dir / "source" / "pyproject.toml").exists()
        meta = json.loads((out_dir / "metadata.json").read_text())
        assert meta["confidence"]["score"] == 100

    def test_refuses_existing_output_without_force(self, python_clone_url, tmp_path, capsys):
        out_dir = tmp_path / "repro"
        out_dir.mkdir()
        code = main(
            [
                "build",
                URL,
                "--issue-file",
                str(FIXTURES / "python_issue.json"),
                "--clone-url",
                python_clone_url,
                "--output",
                str(out_dir),
            ]
        )
        captured = capsys.readouterr()
        assert code == 1
        assert "already exists" in captured.err

    def test_force_replaces(self, python_clone_url, tmp_path, capsys):
        out_dir = tmp_path / "repro"
        out_dir.mkdir()
        (out_dir / "stale.txt").write_text("old")
        code = main(
            [
                "build",
                URL,
                "--issue-file",
                str(FIXTURES / "python_issue.json"),
                "--clone-url",
                python_clone_url,
                "--output",
                str(out_dir),
                "--force",
            ]
        )
        capsys.readouterr()
        assert code == 0
        assert not (out_dir / "stale.txt").exists()


class TestVerify:
    """The full pipeline through verify, on the host, offline.

    Every case runs real commands against `crash_repo`, whose scripts import
    nothing, so no verdict here depends on pip reaching an index.
    """

    @pytest.fixture
    def crash_clone_url(self, crash_repo: Path) -> str:
        return make_git_repo(crash_repo)

    def _verify(self, tmp_path, clone_url, issue_path: Path) -> int:
        return main(
            [
                "verify",
                URL,
                "--issue-file",
                str(issue_path),
                "--clone-url",
                clone_url,
                "--output",
                str(tmp_path / "repro"),
                "--no-docker",
                "--timeout",
                "120",
            ]
        )

    def test_the_reported_failure_is_reproduced(self, crash_clone_url, tmp_path, capsys):
        issue = write_issue(
            tmp_path / "issue.json",
            "python3 tools/repro.py",
            "ValueError: invalid literal for int() with base 10: '-'",
        )
        code = self._verify(tmp_path, crash_clone_url, issue)
        out = capsys.readouterr().out
        assert code == 0
        assert "Verification: REPRODUCED" in out
        assert "exception: match" in out
        assert "message:   exact" in out

    def test_a_different_exception_is_not_a_reproduction(self, crash_clone_url, tmp_path, capsys):
        issue = write_issue(
            tmp_path / "issue.json", "python3 tools/repro.py", "KeyError: 'currency'"
        )
        code = self._verify(tmp_path, crash_clone_url, issue)
        out = capsys.readouterr().out
        assert code == 1
        assert "Verification: DIFFERENT-FAILURE" in out
        assert "the issue reports KeyError, the run raised ValueError" in out

    def test_a_clean_run_does_not_reproduce(self, crash_clone_url, tmp_path, capsys):
        issue = write_issue(tmp_path / "issue.json", "python3 tools/ok.py", "ValueError: boom")
        code = self._verify(tmp_path, crash_clone_url, issue)
        out = capsys.readouterr().out
        assert code == 1
        assert "Verification: NOT-REPRODUCED" in out
        assert "exited 0" in out

    def test_a_setup_failure_is_not_reported_as_a_reproduction(
        self, crash_clone_url, tmp_path, capsys
    ):
        issue = write_issue(
            tmp_path / "issue.json",
            "pip install -r requirements-dev.txt\npython3 tools/repro.py",
            "ValueError: invalid literal for int() with base 10: '-'",
        )
        code = self._verify(tmp_path, crash_clone_url, issue)
        out = capsys.readouterr().out
        assert code == 2
        assert "Verification: ENVIRONMENT-FAILURE" in out
        assert "requirements-dev.txt" in out
        assert "says nothing about the bug" in out

    def test_a_lone_setup_command_is_could_not_tell(self, crash_clone_url, tmp_path, capsys):
        """The last command is always the reproduction step, so a one-line
        script that fails is judged on its signature, not called setup."""
        issue = write_issue(
            tmp_path / "issue.json",
            "pip install -r requirements-dev.txt",
            "ValueError: invalid literal for int() with base 10: '-'",
        )
        code = self._verify(tmp_path, crash_clone_url, issue)
        out = capsys.readouterr().out
        assert code == 2
        assert "Verification: UNKNOWN" in out

    def test_a_vague_issue_cannot_be_verified(self, crash_clone_url, tmp_path, capsys):
        code = self._verify(tmp_path, crash_clone_url, FIXTURES / "vague_issue.json")
        capsys.readouterr()
        # nothing to check against: could not tell, which is not "no"
        assert code == 2

    def test_verdict_and_log_are_written_into_the_workspace(
        self, crash_clone_url, tmp_path, capsys
    ):
        issue = write_issue(
            tmp_path / "issue.json",
            "python3 tools/repro.py",
            "ValueError: invalid literal for int() with base 10: '-'",
        )
        self._verify(tmp_path, crash_clone_url, issue)
        capsys.readouterr()
        meta = json.loads((tmp_path / "repro" / "metadata.json").read_text())
        assert meta["verification"]["verdict"] == "reproduced"
        assert meta["verification"]["match"]["message"] == "exact"
        assert "ValueError" in (tmp_path / "repro" / "verify.log").read_text()

    def test_verify_builds_the_workspace_when_it_is_missing(
        self, crash_clone_url, tmp_path, capsys
    ):
        issue = write_issue(tmp_path / "issue.json", "python3 tools/ok.py", "ValueError: boom")
        self._verify(tmp_path, crash_clone_url, issue)
        out = capsys.readouterr().out
        assert "Workspace written to" in out
        assert (tmp_path / "repro" / "reproduce.sh").exists()

    def test_docker_is_the_default_and_missing_docker_says_so(
        self, crash_clone_url, tmp_path, capsys, monkeypatch
    ):
        monkeypatch.setattr("issue2repro.verify.shutil.which", lambda _: None)
        issue = write_issue(tmp_path / "issue.json", "python3 tools/ok.py", "ValueError: boom")
        code = main(
            [
                "verify",
                URL,
                "--issue-file",
                str(issue),
                "--clone-url",
                crash_clone_url,
                "--output",
                str(tmp_path / "repro"),
            ]
        )
        captured = capsys.readouterr()
        assert code == 2
        assert "docker was not found" in captured.err
        assert "--no-docker" in captured.err


class TestErrors:
    def test_bad_url_is_clean_error(self, capsys):
        code = main(["inspect", "not-a-url", "--issue-file", "x.json"])
        captured = capsys.readouterr()
        assert code == 1
        assert "could not parse" in captured.err

    def test_missing_issue_file(self, capsys):
        code = main(["inspect", URL, "--issue-file", "/nonexistent/issue.json"])
        captured = capsys.readouterr()
        assert code == 1
        assert "not found" in captured.err

    def test_bad_clone_url(self, tmp_path, capsys):
        code = main(
            [
                "build",
                URL,
                "--issue-file",
                str(FIXTURES / "python_issue.json"),
                "--clone-url",
                f"file://{tmp_path}/does-not-exist",
                "--output",
                str(tmp_path / "repro"),
            ]
        )
        captured = capsys.readouterr()
        assert code == 1
        assert "git clone" in captured.err
