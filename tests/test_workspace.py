import json
from pathlib import Path

from issue2repro.confidence import score_confidence
from issue2repro.detect import detect_project
from issue2repro.extract import extract_signals
from issue2repro.models import Analysis, Signals
from issue2repro.workspace import (
    render_dockerfile,
    render_issue_md,
    render_reproduce_sh,
    write_workspace,
)


def build_analysis(issue, repo: Path, signals=None):
    signals = signals if signals is not None else extract_signals(issue.full_text)
    project = detect_project(repo)
    report, mapped = score_confidence(signals, project, repo)
    return Analysis(issue=issue, signals=signals, project=project, confidence=report), mapped


class TestReproduceSh:
    def test_explicit_commands_win(self, python_issue, python_repo):
        analysis, mapped = build_analysis(python_issue, python_repo)
        script = render_reproduce_sh(analysis, mapped)
        assert "pip install -e ." in script
        assert "python -m pytest tests/test_parser.py -x" in script
        assert "Explicit reproduction steps" in script
        assert "WARNING" in script

    def test_python_gets_isolated_venv(self, python_issue, python_repo):
        analysis, mapped = build_analysis(python_issue, python_repo)
        script = render_reproduce_sh(analysis, mapped)
        assert "python3 -m venv" in script
        assert ".venv/bin/activate" in script

    def test_node_gets_no_venv(self, node_issue, node_repo):
        analysis, mapped = build_analysis(node_issue, node_repo)
        script = render_reproduce_sh(analysis, mapped)
        assert "venv" not in script

    def test_fallback_scopes_pytest_to_implicated_test_files(self, python_issue, python_repo):
        analysis, mapped = build_analysis(python_issue, python_repo)
        analysis.signals.commands = []
        script = render_reproduce_sh(analysis, mapped)
        assert "detected test command" in script
        assert "pip install -e ." in script
        assert "python -m pytest tests/test_parser.py" in script
        # library files implicated by the trace are not passed to pytest
        assert "pytest tests/test_parser.py src/mypkg/parser.py" not in script

    def test_fallback_without_test_files_runs_plain_command(self, python_issue, python_repo):
        analysis, _ = build_analysis(python_issue, python_repo, signals=Signals())
        script = render_reproduce_sh(analysis, [])
        assert script.rstrip().endswith("python -m pytest")

    def test_nothing_detected_exits_2(self, vague_issue, tmp_path):
        analysis, mapped = build_analysis(vague_issue, tmp_path)
        script = render_reproduce_sh(analysis, mapped)
        assert "exit 2" in script


class TestDockerfile:
    def test_python_base_and_installs(self, python_issue, python_repo):
        analysis, _ = build_analysis(python_issue, python_repo)
        dockerfile = render_dockerfile(analysis)
        assert "FROM python:3.12-slim" in dockerfile
        assert "RUN pip install -e ." in dockerfile
        assert 'CMD ["bash", "reproduce.sh"]' in dockerfile

    def test_node_base(self, node_issue, node_repo):
        analysis, _ = build_analysis(node_issue, node_repo)
        dockerfile = render_dockerfile(analysis)
        assert "FROM node:20-slim" in dockerfile
        assert "RUN npm install" in dockerfile

    def test_unknown_language_falls_back(self, vague_issue, tmp_path):
        analysis, _ = build_analysis(vague_issue, tmp_path)
        dockerfile = render_dockerfile(analysis)
        assert "FROM debian:bookworm-slim" in dockerfile
        assert "no install steps inferred" in dockerfile


class TestIssueMd:
    def test_contains_title_body_comments_and_link(self, python_issue, python_repo):
        analysis, _ = build_analysis(python_issue, python_repo)
        text = render_issue_md(analysis)
        assert text.startswith("# Parser crashes on unicode input")
        assert python_issue.ref.issue_url in text
        assert "## Comment 1" in text
        assert "Reproduced on main" in text


class TestWriteWorkspace:
    def test_writes_all_files(self, python_issue, python_repo, tmp_path):
        analysis, mapped = build_analysis(python_issue, python_repo)
        out = tmp_path / "repro"
        written = write_workspace(analysis, mapped, out)
        names = sorted(p.name for p in written)
        assert names == ["Dockerfile", "issue.md", "metadata.json", "reproduce.sh"]
        assert (out / "reproduce.sh").stat().st_mode & 0o111

    def test_metadata_structure(self, python_issue, python_repo, tmp_path):
        analysis, mapped = build_analysis(python_issue, python_repo)
        write_workspace(analysis, mapped, tmp_path / "repro")
        meta = json.loads((tmp_path / "repro" / "metadata.json").read_text())
        assert meta["issue"]["owner"] == "acme"
        assert meta["issue"]["number"] == 7
        assert meta["project"]["language"] == "python"
        assert meta["confidence"]["score"] == 100
        assert len(meta["confidence"]["components"]) == 4
        assert "src/mypkg/parser.py" in meta["trace_paths_in_clone"]
        assert meta["signals"]["commands"]
