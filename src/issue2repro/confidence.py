"""Honest confidence scoring for a generated reproduction.

The score is a weighted sum of concrete, checkable facts. Every component
carries the reason it earned (or did not earn) its points, so the printed
breakdown explains exactly what was inferred and why.
"""

from __future__ import annotations

from pathlib import Path

from issue2repro.models import (
    ConfidenceComponent,
    ConfidenceReport,
    ProjectInfo,
    Signals,
    StackTrace,
)

_SKIP_FRAGMENTS = ("site-packages", "node_modules", "dist-packages", "/lib/python", "node:internal")

WEIGHT_COMMANDS = 35
WEIGHT_TRACE = 25
WEIGHT_TEST = 20
WEIGHT_LANGUAGE = 20


def _project_frames(traces: list[StackTrace]) -> list[str]:
    """Frame paths that plausibly belong to the project (not stdlib/deps)."""
    paths: list[str] = []
    for trace in traces:
        for frame in trace.frames:
            if any(fragment in frame.path for fragment in _SKIP_FRAGMENTS):
                continue
            if frame.path not in paths:
                paths.append(frame.path)
    return paths


def map_trace_paths(traces: list[StackTrace], source_dir: Path) -> list[str]:
    """Map stack-trace frame paths to files that exist in the clone.

    Absolute paths from someone else's machine are matched by suffix:
    '/home/x/proj/src/app.py' matches 'src/app.py' in the clone.
    Returns clone-relative paths, deduplicated, in frame order.
    """
    candidates = _project_frames(traces)
    if not candidates:
        return []
    by_name: dict[str, list[Path]] = {}
    for path in source_dir.rglob("*"):
        if path.is_file() and ".git" not in path.parts:
            by_name.setdefault(path.name, []).append(path)
    mapped: list[str] = []
    for raw in candidates:
        parts = [p for p in raw.replace("\\", "/").split("/") if p and p != "."]
        if not parts:
            continue
        for existing in by_name.get(parts[-1], []):
            rel = existing.relative_to(source_dir)
            rel_parts = list(rel.parts)
            n = min(len(parts), len(rel_parts))
            if parts[-n:] == rel_parts[-n:] or len(parts) == 1:
                rel_str = str(rel)
                if rel_str not in mapped:
                    mapped.append(rel_str)
                break
    return mapped


def score_confidence(
    signals: Signals, project: ProjectInfo, source_dir: Path
) -> tuple[ConfidenceReport, list[str]]:
    """Build the confidence breakdown. Also returns the mapped trace paths."""
    components: list[ConfidenceComponent] = []

    n_cmds = len(signals.commands)
    components.append(
        ConfidenceComponent(
            name="explicit repro commands",
            weight=WEIGHT_COMMANDS,
            earned=WEIGHT_COMMANDS if n_cmds else 0,
            reason=(
                f"{n_cmds} command(s) found in fenced shell blocks"
                if n_cmds
                else "no runnable commands found in the issue text"
            ),
        )
    )

    mapped = map_trace_paths(signals.traces, source_dir)
    if signals.traces and mapped:
        trace_earned = WEIGHT_TRACE
        shown = ", ".join(mapped[:3])
        trace_reason = f"{signals.traces[0].language} stack trace maps to existing file(s): {shown}"
    elif signals.traces:
        trace_earned = WEIGHT_TRACE // 2
        trace_reason = (
            f"{signals.traces[0].language} stack trace found, "
            "but its frames do not match files in the clone"
        )
    else:
        trace_earned = 0
        trace_reason = "no recognizable stack trace in the issue text"
    components.append(
        ConfidenceComponent(
            name="stack trace",
            weight=WEIGHT_TRACE,
            earned=trace_earned,
            reason=trace_reason,
        )
    )

    components.append(
        ConfidenceComponent(
            name="test command",
            weight=WEIGHT_TEST,
            earned=WEIGHT_TEST if project.test_command else 0,
            reason=(
                f"detected from manifests: {project.test_command}"
                if project.test_command
                else "no test runner configuration found"
            ),
        )
    )

    components.append(
        ConfidenceComponent(
            name="language detected",
            weight=WEIGHT_LANGUAGE,
            earned=WEIGHT_LANGUAGE if project.language else 0,
            reason=(
                f"{project.language} (manifests: {', '.join(project.manifests)})"
                if project.language
                else "no Python or Node manifest at the repository root"
            ),
        )
    )

    return ConfidenceReport(components=components), mapped
