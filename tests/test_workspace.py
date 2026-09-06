import json
from pathlib import Path

from issue2repro.confidence import score_confidence
from issue2repro.detect import detect_project
from issue2repro.extract import extract_signals
from issue2repro.models import Analysis, Signals
from issue2repro.workspace import (
    STEP_MARKER_PREFIX,
    classify_step,
    plan_steps,
    render_dockerfile,
    render_dockerignore,
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


class TestStepClassification:
    def test_installers_are_setup(self):
        for command in (
            "pip install -e .",
            "pip3 install -r requirements.txt",
            "uv pip install pytest",
            "poetry install",
            "npm install",
            "npm ci",
            "yarn add left-pad",
            "apt-get install -y libpq-dev",
        ):
            assert classify_step(command) == "setup", command

    def test_environment_and_navigation_are_setup(self):
        for command in ("cd packages/core", "export DEBUG=1", "git checkout v2.1.0"):
            assert classify_step(command) == "setup", command

    def test_a_package_manager_not_installing_is_a_reproduction(self):
        # the distinction the classifier exists for: npm test is the bug, not the setup
        assert classify_step("npm test") == "repro"
        assert classify_step("npm run build") == "repro"
        assert classify_step("poetry run pytest") == "repro"

    def test_test_runners_are_reproduction_steps(self):
        for command in ("python -m pytest -x", "pytest tests/", "node --test", "make check"):
            assert classify_step(command) == "repro", command

    def test_variable_prefixes_are_looked_through(self):
        assert classify_step("PIP_NO_CACHE=1 pip install .") == "setup"
        assert classify_step("TZ=UTC python -m pytest") == "repro"

    def test_the_last_step_is_always_the_reproduction(self, python_issue, python_repo):
        analysis, mapped = build_analysis(python_issue, python_repo)
        analysis.signals.commands = ["pip install -e .", "pip install pytest"]
        steps = plan_steps(analysis, mapped)
        assert [s.kind for s in steps] == ["setup", "repro"]

    def test_explicit_commands_are_planned_in_order(self, python_issue, python_repo):
        analysis, mapped = build_analysis(python_issue, python_repo)
        steps = plan_steps(analysis, mapped)
        assert [s.command for s in steps] == [
            "pip install -e .",
            "python -m pytest tests/test_parser.py -x",
        ]
        assert [s.kind for s in steps] == ["setup", "repro"]
        assert [s.index for s in steps] == [1, 2]

    def test_nothing_to_run_plans_no_steps(self, vague_issue, tmp_path):
        analysis, mapped = build_analysis(vague_issue, tmp_path)
        assert plan_steps(analysis, mapped) == []


class TestStepMarkers:
    def test_every_step_is_marked(self, python_issue, python_repo):
        analysis, mapped = build_analysis(python_issue, python_repo)
        script = render_reproduce_sh(analysis, mapped)
        assert "i2r_step 1 setup" in script
        assert "i2r_step 2 repro" in script
        assert STEP_MARKER_PREFIX in script

    def test_markers_are_silent_unless_verify_asks(self, python_issue, python_repo):
        analysis, mapped = build_analysis(python_issue, python_repo)
        script = render_reproduce_sh(analysis, mapped)
        assert 'if [ "${ISSUE2REPRO_TRACE:-}" = "1" ]; then' in script

    def test_a_script_with_nothing_to_run_still_exits_2(self, vague_issue, tmp_path):
        analysis, mapped = build_analysis(vague_issue, tmp_path)
        script = render_reproduce_sh(analysis, mapped)
        assert "exit 2" in script
        assert "i2r_step 1" not in script


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
        assert names == [".dockerignore", "Dockerfile", "issue.md", "metadata.json", "reproduce.sh"]
        assert (out / "reproduce.sh").stat().st_mode & 0o111

    def test_dockerignore_keeps_host_artifacts_out_of_the_image(self):
        text = render_dockerignore()
        assert ".venv/" in text
        assert "verify.log" in text

    def test_metadata_records_the_step_plan(self, python_issue, python_repo, tmp_path):
        analysis, mapped = build_analysis(python_issue, python_repo)
        write_workspace(analysis, mapped, tmp_path / "repro")
        meta = json.loads((tmp_path / "repro" / "metadata.json").read_text())
        assert meta["reproduce_steps"] == [
            {"index": 1, "kind": "setup", "command": "pip install -e ."},
            {
                "index": 2,
                "kind": "repro",
                "command": "python -m pytest tests/test_parser.py -x",
            },
        ]

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
