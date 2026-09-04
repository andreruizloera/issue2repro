import json
from pathlib import Path

from issue2repro.detect import detect_project


class TestPythonDetection:
    def test_pyproject_with_pytest_dep(self, python_repo: Path):
        info = detect_project(python_repo)
        assert info.language == "python"
        assert info.manifests == ["pyproject.toml"]
        assert info.install_commands == ["pip install -e ."]
        assert info.test_command == "python -m pytest"

    def test_requirements_only_no_tests(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests\n")
        info = detect_project(tmp_path)
        assert info.language == "python"
        assert info.install_commands == ["pip install -r requirements.txt"]
        assert info.test_command is None

    def test_requirements_with_tests_dir(self, tmp_path: Path):
        (tmp_path / "requirements.txt").write_text("requests\n")
        (tmp_path / "tests").mkdir()
        info = detect_project(tmp_path)
        assert info.test_command == "python -m pytest"

    def test_pytest_ini_marker(self, tmp_path: Path):
        (tmp_path / "setup.py").write_text("from setuptools import setup\nsetup()\n")
        (tmp_path / "pytest.ini").write_text("[pytest]\n")
        info = detect_project(tmp_path)
        assert info.test_command == "python -m pytest"
        assert "pip install -e ." in info.install_commands

    def test_tool_pytest_section(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\nversion = "0"\n\n[tool.pytest.ini_options]\n'
        )
        info = detect_project(tmp_path)
        assert info.test_command == "python -m pytest"

    def test_broken_pyproject_still_python(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("not [valid toml")
        info = detect_project(tmp_path)
        assert info.language == "python"


class TestNodeDetection:
    def test_scripts_detected(self, node_repo: Path):
        info = detect_project(node_repo)
        assert info.language == "node"
        assert info.install_commands == ["npm install"]
        assert info.test_command == "npm test"
        assert info.build_command == "npm run build"

    def test_npm_placeholder_test_ignored(self, tmp_path: Path):
        (tmp_path / "package.json").write_text(
            json.dumps({"scripts": {"test": 'echo "Error: no test specified" && exit 1'}})
        )
        info = detect_project(tmp_path)
        assert info.language == "node"
        assert info.test_command is None

    def test_broken_package_json(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{broken")
        info = detect_project(tmp_path)
        assert info.language == "node"
        assert info.test_command is None


class TestAmbiguousAndUnknown:
    def test_empty_dir_unknown(self, tmp_path: Path):
        info = detect_project(tmp_path)
        assert info.language is None
        assert info.test_command is None

    def test_python_wins_over_node(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
        (tmp_path / "package.json").write_text("{}")
        assert detect_project(tmp_path).language == "python"
