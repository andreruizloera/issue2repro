"""Infer project type and test/build commands from a cloned repository."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from issue2repro.models import ProjectInfo

_PY_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
_PY_TEST_MARKERS = ("pytest.ini", "tox.ini", "conftest.py")
_NPM_PLACEHOLDER = 'echo "Error: no test specified" && exit 1'

_MAVEN_MANIFESTS = ("pom.xml",)
_GRADLE_MANIFESTS = (
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
)

# Surefire 3.x aborts the build when -Dtest matches nothing. Left unguarded,
# a bad scope guess would exit nonzero and read as a reproduction, so the
# scoped command carries the guard. The flag every tutorial names,
# -DfailIfNoTests=false, is NOT the one that works on Surefire 3.5: it is
# silently ignored and the build still fails. Measured, not assumed.
MAVEN_NO_MATCH_GUARD = "-Dsurefire.failIfNoSpecifiedTests=false"


def _python_info(root: Path, manifests: list[str]) -> ProjectInfo:
    info = ProjectInfo(language="python", manifests=manifests)
    if "pyproject.toml" in manifests or "setup.py" in manifests:
        info.install_commands.append("pip install -e .")
    if "requirements.txt" in manifests:
        info.install_commands.append("pip install -r requirements.txt")

    pytest_detected = any((root / m).exists() for m in _PY_TEST_MARKERS)
    if (root / "tests").is_dir() or (root / "test").is_dir():
        pytest_detected = True
    if "pyproject.toml" in manifests:
        try:
            data = tomllib.loads((root / "pyproject.toml").read_text())
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        tool = data.get("tool", {})
        if "pytest" in tool:
            pytest_detected = True
        deps: list[str] = list(data.get("project", {}).get("dependencies", []))
        for extra in data.get("project", {}).get("optional-dependencies", {}).values():
            deps.extend(extra)
        if any(d.lower().startswith("pytest") for d in deps):
            pytest_detected = True
    if pytest_detected:
        info.test_command = "python -m pytest"
    return info


def _node_info(root: Path) -> ProjectInfo:
    info = ProjectInfo(language="node", manifests=["package.json"])
    info.install_commands.append("npm install")
    try:
        data = json.loads((root / "package.json").read_text())
    except (json.JSONDecodeError, OSError):
        data = {}
    scripts = data.get("scripts", {}) if isinstance(data, dict) else {}
    test_script = scripts.get("test")
    if test_script and test_script.strip() != _NPM_PLACEHOLDER:
        info.test_command = "npm test"
    if scripts.get("build"):
        info.build_command = "npm run build"
    return info


def _has_jvm_tests(root: Path) -> bool:
    """True when the standard Maven/Gradle test source set exists.

    Checked at the root for a single-module project and one level down for a
    multi-module one, which is where an aggregator pom puts its modules.
    """
    if (root / "src" / "test").is_dir():
        return True
    return any(child.is_dir() and (child / "src" / "test").is_dir() for child in root.iterdir())


def _jvm_info(root: Path, tool: str, manifests: list[str]) -> ProjectInfo:
    """Build commands for a Maven or Gradle project.

    No install step is generated. Both tools resolve dependencies as part of
    the task they are asked to run, so there is nothing separate to install,
    and inventing a warm-up goal would add a way for the workspace to fail
    before it reaches the bug.

    Gradle is driven through the project's own wrapper when one is committed,
    which is what the project pins its version with; only a repository with no
    wrapper falls back to whatever `gradle` is on PATH. `--no-daemon` keeps a
    background daemon from outliving the reproduction.
    """
    info = ProjectInfo(language="jvm", manifests=manifests, build_tool=tool)
    if tool == "maven":
        info.build_command = "mvn -B -DskipTests package"
        if _has_jvm_tests(root):
            info.test_command = "mvn -B test"
        return info

    gradle = "./gradlew" if (root / "gradlew").exists() else "gradle"
    info.build_command = f"{gradle} --no-daemon build -x test"
    if _has_jvm_tests(root):
        info.test_command = f"{gradle} --no-daemon test"
    return info


def detect_project(root: Path) -> ProjectInfo:
    """Classify a repository as Python, JVM, Node, or unknown.

    When several ecosystems are present, the one whose primary manifest sits
    at the repository root wins in the order Python, JVM, Node. Python stays
    first because a package.json in a Python repo is usually docs tooling;
    JVM comes before Node for the same reason, since a package.json beside a
    pom.xml is usually frontend assets for a Java service.

    A repository carrying both a pom.xml and a build.gradle is genuinely
    ambiguous and this is a stated heuristic: Maven wins, because pom.xml is
    the more definitive of the two manifests. A project that has migrated to
    Gradle but kept a stale pom.xml will be detected as Maven.
    """
    py_manifests = [m for m in _PY_MANIFESTS if (root / m).exists()]
    if py_manifests:
        return _python_info(root, py_manifests)

    maven_manifests = [m for m in _MAVEN_MANIFESTS if (root / m).exists()]
    if maven_manifests:
        return _jvm_info(root, "maven", maven_manifests)
    gradle_manifests = [m for m in _GRADLE_MANIFESTS if (root / m).exists()]
    if gradle_manifests:
        return _jvm_info(root, "gradle", gradle_manifests)

    if (root / "package.json").exists():
        return _node_info(root)
    return ProjectInfo(language=None)
