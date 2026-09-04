from pathlib import Path

from issue2repro.confidence import map_trace_paths, score_confidence
from issue2repro.detect import detect_project
from issue2repro.extract import extract_signals
from issue2repro.models import ProjectInfo, Signals, StackFrame, StackTrace


def _component(report, name):
    return next(c for c in report.components if c.name == name)


class TestMapTracePaths:
    def test_relative_path_maps(self, python_repo: Path):
        traces = [StackTrace("python", [StackFrame("tests/test_parser.py", 9)])]
        assert map_trace_paths(traces, python_repo) == ["tests/test_parser.py"]

    def test_absolute_foreign_path_maps_by_suffix(self, python_repo: Path):
        traces = [StackTrace("python", [StackFrame("/home/alice/project/src/mypkg/parser.py", 42)])]
        assert map_trace_paths(traces, python_repo) == ["src/mypkg/parser.py"]

    def test_stdlib_and_site_packages_skipped(self, python_repo: Path):
        traces = [
            StackTrace(
                "python",
                [StackFrame("/usr/lib/python3.12/site-packages/x/parser.py", 1)],
            )
        ]
        assert map_trace_paths(traces, python_repo) == []

    def test_node_internal_skipped(self, node_repo: Path):
        traces = [StackTrace("node", [StackFrame("node:internal/modules/cjs/loader", 1)])]
        assert map_trace_paths(traces, node_repo) == []

    def test_nonexistent_file_unmapped(self, python_repo: Path):
        traces = [StackTrace("python", [StackFrame("src/other/ghost.py", 3)])]
        assert map_trace_paths(traces, python_repo) == []


class TestScoring:
    def test_full_house_scores_100(self, python_issue, python_repo):
        signals = extract_signals(python_issue.full_text)
        project = detect_project(python_repo)
        report, mapped = score_confidence(signals, project, python_repo)
        assert report.score == 100
        assert "src/mypkg/parser.py" in mapped

    def test_vague_issue_scores_language_only(self, vague_issue, python_repo):
        signals = extract_signals(vague_issue.full_text)
        project = detect_project(python_repo)
        report, mapped = score_confidence(signals, project, python_repo)
        assert mapped == []
        assert _component(report, "explicit repro commands").earned == 0
        assert _component(report, "stack trace").earned == 0
        assert report.score == 40  # test command + language

    def test_unmapped_trace_earns_half(self, tmp_path: Path):
        signals = Signals(traces=[StackTrace("python", [StackFrame("ghost.py", 1)])])
        project = ProjectInfo(language=None)
        report, mapped = score_confidence(signals, project, tmp_path)
        assert mapped == []
        component = _component(report, "stack trace")
        assert component.earned == component.weight // 2
        assert "do not match" in component.reason

    def test_nothing_scores_zero(self, tmp_path: Path):
        report, _ = score_confidence(Signals(), ProjectInfo(), tmp_path)
        assert report.score == 0

    def test_every_component_has_a_reason(self, node_issue, node_repo):
        signals = extract_signals(node_issue.full_text)
        project = detect_project(node_repo)
        report, _ = score_confidence(signals, project, node_repo)
        assert len(report.components) == 4
        assert all(c.reason for c in report.components)

    def test_node_full_house(self, node_issue, node_repo):
        signals = extract_signals(node_issue.full_text)
        project = detect_project(node_repo)
        report, mapped = score_confidence(signals, project, node_repo)
        assert report.score == 100
        assert "src/config.js" in mapped
