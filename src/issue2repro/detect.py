"""Infer project type and test/build commands from a cloned repository."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from issue2repro.models import ProjectInfo

_PY_MANIFESTS = ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
_PY_TEST_MARKERS = ("pytest.ini", "tox.ini", "conftest.py")
_NPM_PLACEHOLDER = 'echo "Error: no test specified" && exit 1'


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


def detect_project(root: Path) -> ProjectInfo:
    """Classify a repository as Python, Node, or unknown.

    When both ecosystems are present, the one whose primary manifest sits
    at the repository root wins Python-first, since a package.json in a
    Python repo is usually docs tooling.
    """
    py_manifests = [m for m in _PY_MANIFESTS if (root / m).exists()]
    has_node = (root / "package.json").exists()
    if py_manifests:
        return _python_info(root, py_manifests)
    if has_node:
        return _node_info(root)
    return ProjectInfo(language=None)
