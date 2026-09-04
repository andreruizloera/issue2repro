"""issue2repro command line interface: inspect, build, run."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from issue2repro import __version__
from issue2repro.confidence import score_confidence
from issue2repro.detect import detect_project
from issue2repro.extract import extract_signals
from issue2repro.github import (
    Issue2ReproError,
    clone_repo,
    fetch_issue,
    load_issue_file,
    parse_issue_url,
)
from issue2repro.models import Analysis, Issue
from issue2repro.workspace import write_workspace


def _get_issue(args: argparse.Namespace) -> Issue:
    ref = parse_issue_url(args.url)
    if args.issue_file:
        return load_issue_file(ref, Path(args.issue_file))
    return fetch_issue(ref)


def _analyze(issue: Issue, source_dir: Path) -> tuple[Analysis, list[str]]:
    signals = extract_signals(issue.full_text)
    project = detect_project(source_dir)
    confidence, mapped = score_confidence(signals, project, source_dir)
    return Analysis(issue=issue, signals=signals, project=project, confidence=confidence), mapped


def _print_analysis(analysis: Analysis) -> None:
    issue = analysis.issue
    project = analysis.project
    signals = analysis.signals
    print(f"Issue: {issue.ref.slug}: {issue.title}")
    print(
        f"Language: {project.language or 'unknown'}"
        + (f" ({', '.join(project.manifests)})" if project.manifests else "")
    )
    if project.install_commands:
        print(f"Install: {'; '.join(project.install_commands)}")
    print(f"Test command: {project.test_command or 'not detected'}")
    if project.build_command:
        print(f"Build command: {project.build_command}")
    print("Signals:")
    if signals.commands:
        print(f"  explicit commands ({len(signals.commands)}):")
        for cmd in signals.commands:
            print(f"    $ {cmd}")
    else:
        print("  explicit commands: none")
    if signals.traces:
        for trace in signals.traces:
            error = f" ({trace.error})" if trace.error else ""
            print(f"  stack trace: {trace.language}, {len(trace.frames)} frame(s){error}")
    else:
        print("  stack traces: none")
    if signals.filenames:
        print(f"  filenames mentioned: {', '.join(signals.filenames[:8])}")
    print()
    print(f"Reproduction confidence: {analysis.confidence.score}%")
    for c in analysis.confidence.components:
        print(f"  [{c.earned:>2}/{c.weight}] {c.name}: {c.reason}")


def cmd_inspect(args: argparse.Namespace) -> int:
    issue = _get_issue(args)
    clone_url = args.clone_url or issue.ref.repo_url
    with tempfile.TemporaryDirectory(prefix="issue2repro-") as tmp:
        source_dir = Path(tmp) / "source"
        clone_repo(clone_url, source_dir)
        analysis, _ = _analyze(issue, source_dir)
        _print_analysis(analysis)
    return 0


def _build_workspace(args: argparse.Namespace) -> Path:
    issue = _get_issue(args)
    clone_url = args.clone_url or issue.ref.repo_url
    out_dir = Path(args.output)
    if out_dir.exists():
        if not args.force:
            raise Issue2ReproError(
                f"output directory {out_dir} already exists. Pass --force to replace it."
            )
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    source_dir = out_dir / "source"
    clone_repo(clone_url, source_dir)
    analysis, mapped = _analyze(issue, source_dir)
    write_workspace(analysis, mapped, out_dir)
    _print_analysis(analysis)
    print()
    print(f"Workspace written to {out_dir}/")
    for name in ("source/", "issue.md", "metadata.json", "reproduce.sh", "Dockerfile"):
        print(f"  {name}")
    return out_dir


def cmd_build(args: argparse.Namespace) -> int:
    _build_workspace(args)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    out_dir = Path(args.output)
    script = out_dir / "reproduce.sh"
    if not script.exists():
        out_dir = _build_workspace(args)
        script = out_dir / "reproduce.sh"
        print()
    print("WARNING: reproduce.sh executes code from the cloned repository.")
    print(f"Running {script} ...")
    print(flush=True)
    result = subprocess.run(["bash", str(script)], check=False)
    print()
    if result.returncode == 0:
        print("reproduce.sh exited 0: the steps ran cleanly (the bug may not have reproduced).")
    else:
        print(
            f"reproduce.sh exited {result.returncode}: "
            "a nonzero exit usually means the reported failure reproduced."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="issue2repro",
        description="Turn a GitHub bug report into a reproducible local environment.",
    )
    parser.add_argument("--version", action="version", version=f"issue2repro {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, help_text in (
        ("inspect", cmd_inspect, "analyze an issue and print the confidence breakdown"),
        (
            "build",
            cmd_build,
            "write the reproduction workspace (clone, issue.md, reproduce.sh, Dockerfile)",
        ),
        ("run", cmd_run, "build if needed, then execute reproduce.sh locally"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("url", help="GitHub issue URL (or OWNER/REPO#NUMBER)")
        p.add_argument(
            "--issue-file",
            help="read the issue payload from a local JSON file instead of the gh CLI",
        )
        p.add_argument(
            "--clone-url",
            help="clone the repository from this URL instead (supports file://)",
        )
        p.add_argument(
            "-o",
            "--output",
            default="repro",
            help="workspace directory for build/run (default: repro)",
        )
        p.add_argument(
            "--force",
            action="store_true",
            help="replace the output directory if it already exists",
        )
        p.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Issue2ReproError as exc:
        print(f"issue2repro: error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("issue2repro: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
