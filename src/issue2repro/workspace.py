"""Generate the reproduction workspace: issue.md, metadata.json, reproduce.sh, Dockerfile."""

from __future__ import annotations

import json
from pathlib import Path

from issue2repro.detect import MAVEN_NO_MATCH_GUARD
from issue2repro.models import Analysis, ProjectInfo, Step

_DOCKER_BASES = {"python": "python:3.12-slim", "node": "node:20-slim"}

# A Gradle project that commits a wrapper pins its own Gradle version and only
# needs a JDK; one without a wrapper needs Gradle supplied by the image.
_GRADLE_WRAPPER_BASE = "eclipse-temurin:21-jdk"
_GRADLE_BASE = "gradle:8-jdk21"
_MAVEN_BASE = "maven:3.9-eclipse-temurin-21"

_JVM_TEST_SUFFIXES = (".java", ".kt", ".groovy", ".scala")

# Commands that bring the environment up rather than exercise the bug.
# A package manager only counts as setup when it is being asked to install:
# "npm install" is setup, "npm test" is the reproduction.
_PACKAGE_MANAGERS = {
    "pip",
    "pip3",
    "uv",
    "poetry",
    "pipenv",
    "conda",
    "npm",
    "yarn",
    "pnpm",
    "bundle",
    "apt",
    "apt-get",
    "brew",
}
_INSTALL_VERBS = {"install", "add", "sync", "ci", "update", "upgrade"}
_SETUP_HEADS = {"cd", "export", "env", "source", ".", "mkdir", "git", "chmod", "ln"}

STEP_MARKER_PREFIX = "##issue2repro:step:"


def render_issue_md(analysis: Analysis) -> str:
    issue = analysis.issue
    lines = [
        f"# {issue.title}",
        "",
        f"Source: {issue.ref.issue_url}",
        "",
        issue.body or "(no body)",
    ]
    for i, comment in enumerate(issue.comments, start=1):
        lines += ["", "---", "", f"## Comment {i}", "", comment]
    return "\n".join(lines) + "\n"


def _jvm_test_classes(mapped_paths: list[str]) -> list[str]:
    """Class names for trace-implicated paths that are themselves JVM tests.

    A path counts as a test when it sits under a `src/test/` source set or
    carries a conventional test suffix in its name. Library classes are
    excluded for the same reason pytest is not pointed at library modules:
    a filter that names one selects nothing.
    """
    classes = []
    for path in mapped_paths:
        name = Path(path).name
        if not name.endswith(_JVM_TEST_SUFFIXES):
            continue
        stem = Path(path).stem
        in_test_tree = "src/test/" in path
        named_test = stem.endswith(("Test", "Tests", "TestCase", "Spec", "IT"))
        if in_test_tree or named_test:
            classes.append(stem)
    return sorted(set(classes))


def _scoped_test_command(analysis: Analysis, mapped_paths: list[str]) -> str:
    """The detected test command, scoped to trace-implicated files when possible.

    pytest is scoped by path, and only to implicated files that are themselves
    test files; pointing pytest at library modules collects nothing.

    Maven is scoped with `-Dtest=`, carrying the guard that keeps a filter
    matching nothing from aborting the build. **Gradle is deliberately not
    scoped**, and this is measured rather than an oversight: a `--tests` filter
    that matches nothing fails the build with `No tests found for given
    includes`, and Gradle's escape hatch for that (`filter {
    failOnNoMatchingTests = false }`) is a build-script setting with no
    command-line equivalent, so using it would mean editing the project's own
    build file. A wrong scope guess would therefore exit nonzero and read as a
    reproduction, which is a wrong verdict rather than a missing one, so Gradle
    runs the whole test task.
    """
    project = analysis.project
    test_command = project.test_command or ""
    if not test_command:
        return test_command

    if "pytest" in test_command:
        test_files = [
            p
            for p in mapped_paths
            if p.endswith(".py") and ("test" in Path(p).name or p.startswith(("tests/", "test/")))
        ]
        if test_files:
            return f"{test_command} {' '.join(test_files)}"
        return test_command

    if project.build_tool == "maven":
        classes = _jvm_test_classes(mapped_paths)
        if classes:
            return f"{test_command} -Dtest={','.join(classes)} {MAVEN_NO_MATCH_GUARD}"
    return test_command


def classify_step(command: str) -> str:
    """Label a command "setup" or "repro".

    A heuristic, and the README says so: a package manager asked to install,
    a directory change, or an environment tweak is setup; anything else is
    taken to be the command that exercises the bug. Getting this wrong costs
    a label on a verdict, never a wrong verdict, because verify only uses it
    to decide whether the failure happened before the reproduction ran.
    """
    parts = command.split()
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts = parts[1:]  # VAR=value prefixes
    if not parts:
        return "setup"
    head = parts[0].rsplit("/", 1)[-1]
    if head in _SETUP_HEADS:
        return "setup"
    if head in _PACKAGE_MANAGERS:
        rest = {p for p in parts[1:] if not p.startswith("-")}
        return "setup" if rest & _INSTALL_VERBS else "repro"
    return "repro"


def plan_steps(analysis: Analysis, mapped_paths: list[str]) -> list[Step]:
    """The ordered commands reproduce.sh will run, each labeled setup or repro.

    The last command is always the reproduction step. A script whose every
    command looks like setup has nothing for verify to observe, and calling
    its last line the reproduction is more useful than reporting that the
    run never reached one.
    """
    project = analysis.project
    if analysis.signals.commands:
        commands = list(analysis.signals.commands)
    else:
        commands = list(project.install_commands)
        scoped = _scoped_test_command(analysis, mapped_paths)
        if scoped:
            commands.append(scoped)
    if not commands:
        return []
    steps = [
        Step(index=i, kind=classify_step(command), command=command)
        for i, command in enumerate(commands, start=1)
    ]
    steps[-1].kind = "repro"
    return steps


def render_reproduce_sh(analysis: Analysis, mapped_paths: list[str]) -> str:
    """Build reproduce.sh.

    Explicit commands from the issue win. Otherwise fall back to the
    manifest install steps plus the detected test command, scoped to files
    the stack trace implicates. Python runs inside a workspace-local venv
    so pip installs never touch the caller's environment.

    Each command is preceded by a step marker that stays silent unless
    `issue2repro verify` sets ISSUE2REPRO_TRACE=1, so running the script by
    hand prints exactly what it printed before this existed.
    """
    issue = analysis.issue
    project = analysis.project
    lines = [
        "#!/usr/bin/env bash",
        f"# Generated by issue2repro for {issue.ref.slug}",
        f"# Issue: {issue.ref.issue_url}",
        "#",
        "# WARNING: this script executes code from the cloned repository.",
        "# Read it before running. A nonzero exit usually means the bug reproduced.",
        "set -euo pipefail",
        'WORKSPACE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        "",
        "# Step markers for `issue2repro verify`, so it can tell a setup failure",
        "# apart from the failure the issue describes. Silent by default.",
        "i2r_step() {",
        '    if [ "${ISSUE2REPRO_TRACE:-}" = "1" ]; then',
        f'        printf \'{STEP_MARKER_PREFIX}%s:%s\\n\' "$1" "$2"',
        "    fi",
        "}",
    ]
    if project.language == "python":
        lines += [
            "",
            "# Isolated environment so installs do not touch your system Python.",
            'if [ ! -d "$WORKSPACE/.venv" ]; then python3 -m venv "$WORKSPACE/.venv"; fi',
            '. "$WORKSPACE/.venv/bin/activate"',
        ]
    lines += ["", 'cd "$WORKSPACE/source"', ""]

    steps = plan_steps(analysis, mapped_paths)
    if not steps:
        lines += [
            "# No explicit steps in the issue and no test command was detected.",
            'echo "issue2repro could not detect a test command for this project." >&2',
            "exit 2",
        ]
        return "\n".join(lines) + "\n"

    if analysis.signals.commands:
        lines.append("# Explicit reproduction steps taken from the issue text.")
    else:
        lines.append("# No explicit steps in the issue; running the detected test command.")
    for step in steps:
        lines.append(f"i2r_step {step.index} {step.kind}")
        lines.append(step.command)
    return "\n".join(lines) + "\n"


def render_dockerignore(analysis: Analysis | None = None) -> str:
    """Keep the verify build context small and free of host-built artifacts.

    Without this, `verify` would upload the workspace venv that `run` leaves
    behind, which is both slow and a Linux image full of macOS binaries.

    The JVM output directories are added only for a JVM project, on purpose.
    `build/` is a plausible name for a directory a repository genuinely
    tracks, and excluding it everywhere would risk dropping real sources from
    the image; inside a Gradle project it is the build output by convention.
    """
    entries = [
        "# Generated by issue2repro.",
        ".venv/",
        "verify.log",
        "source/.git/",
        "**/__pycache__/",
        "**/node_modules/",
    ]
    if analysis is not None and analysis.project.language == "jvm":
        entries += ["**/target/", "**/build/", "**/.gradle/"]
    return "\n".join(entries) + "\n"


def _docker_base(project: ProjectInfo) -> str:
    """Base image for the detected ecosystem.

    JVM projects need the build tool as well as a JDK, except when the
    repository commits a Gradle wrapper, which pins and downloads its own
    Gradle and so only needs the JDK.
    """
    if project.language == "jvm":
        if project.build_tool == "maven":
            return _MAVEN_BASE
        uses_wrapper = (project.test_command or project.build_command or "").startswith("./gradlew")
        return _GRADLE_WRAPPER_BASE if uses_wrapper else _GRADLE_BASE
    return _DOCKER_BASES.get(project.language or "", "debian:bookworm-slim")


def render_dockerfile(analysis: Analysis) -> str:
    """Best-effort Dockerfile for the detected ecosystem.

    It sets up the right base image and manifest install steps; it will not
    magically reproduce every issue (system deps, services, etc. are on you).
    """
    project = analysis.project
    base = _docker_base(project)
    lines = [
        f"# Generated by issue2repro for {analysis.issue.ref.slug} (best effort)",
        f"FROM {base}",
        "WORKDIR /repro",
        "COPY source/ ./source/",
        "COPY reproduce.sh issue.md metadata.json ./",
        "WORKDIR /repro/source",
    ]
    if project.language == "python":
        for cmd in analysis.project.install_commands:
            lines.append(f"RUN {cmd}")
    elif project.language == "node":
        lines.append("RUN npm install")
    elif project.language == "jvm":
        lines.append(f"# {project.build_tool} resolves dependencies as part of the task it runs,")
        lines.append("# so there is no separate install step to bake into a layer.")
    else:
        lines.append("# Unknown ecosystem: no install steps inferred.")
    lines += [
        "WORKDIR /repro",
        'CMD ["bash", "reproduce.sh"]',
    ]
    return "\n".join(lines) + "\n"


def write_workspace(analysis: Analysis, mapped_paths: list[str], out_dir: Path) -> list[Path]:
    """Write everything except source/ (the clone is moved in by the CLI)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    issue_md = out_dir / "issue.md"
    issue_md.write_text(render_issue_md(analysis))
    written.append(issue_md)

    metadata = out_dir / "metadata.json"
    payload = analysis.to_metadata()
    payload["trace_paths_in_clone"] = mapped_paths
    payload["reproduce_steps"] = [
        {"index": s.index, "kind": s.kind, "command": s.command}
        for s in plan_steps(analysis, mapped_paths)
    ]
    metadata.write_text(json.dumps(payload, indent=2) + "\n")
    written.append(metadata)

    reproduce = out_dir / "reproduce.sh"
    reproduce.write_text(render_reproduce_sh(analysis, mapped_paths))
    reproduce.chmod(0o755)
    written.append(reproduce)

    dockerfile = out_dir / "Dockerfile"
    dockerfile.write_text(render_dockerfile(analysis))
    written.append(dockerfile)

    dockerignore = out_dir / ".dockerignore"
    dockerignore.write_text(render_dockerignore(analysis))
    written.append(dockerignore)

    return written
