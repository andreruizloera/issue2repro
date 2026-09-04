"""End-to-end CLI tests. Fully offline: issues come from fixture JSON files
and repositories are cloned over file:// from git repos built in tmp_path."""

import json
import subprocess
from pathlib import Path

import pytest

from issue2repro.cli import main

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
