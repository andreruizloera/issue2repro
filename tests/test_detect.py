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


class TestJvmDetection:
    def test_maven_project(self, maven_repo: Path):
        info = detect_project(maven_repo)
        assert info.language == "jvm"
        assert info.build_tool == "maven"
        assert info.manifests == ["pom.xml"]
        assert info.test_command == "mvn -B test"
        assert info.build_command == "mvn -B -DskipTests package"

    def test_maven_generates_no_install_step(self, maven_repo: Path):
        """Maven resolves dependencies inside the task it is asked to run.

        An invented warm-up goal would only add a way for the workspace to
        fail before it ever reaches the bug.
        """
        assert detect_project(maven_repo).install_commands == []

    def test_gradle_wrapper_is_preferred_over_path_gradle(self, gradle_repo: Path):
        info = detect_project(gradle_repo)
        assert info.language == "jvm"
        assert info.build_tool == "gradle"
        assert info.test_command == "./gradlew --no-daemon test"
        assert info.build_command == "./gradlew --no-daemon build -x test"

    def test_gradle_without_wrapper_falls_back_to_path(self, gradle_repo: Path):
        (gradle_repo / "gradlew").unlink()
        info = detect_project(gradle_repo)
        assert info.test_command == "gradle --no-daemon test"
        assert info.build_command == "gradle --no-daemon build -x test"

    def test_kotlin_gradle_script_detected(self, tmp_path: Path):
        (tmp_path / "build.gradle.kts").write_text("plugins { java }\n")
        (tmp_path / "src" / "test" / "kotlin").mkdir(parents=True)
        info = detect_project(tmp_path)
        assert info.language == "jvm"
        assert info.build_tool == "gradle"
        assert info.manifests == ["build.gradle.kts"]

    def test_no_test_source_set_means_no_test_command(self, tmp_path: Path):
        """A build command is still inferred; there is just nothing to run."""
        (tmp_path / "pom.xml").write_text("<project/>\n")
        (tmp_path / "src" / "main" / "java").mkdir(parents=True)
        info = detect_project(tmp_path)
        assert info.language == "jvm"
        assert info.test_command is None
        assert info.build_command == "mvn -B -DskipTests package"

    def test_multi_module_test_source_set_found_one_level_down(self, tmp_path: Path):
        (tmp_path / "pom.xml").write_text("<project/>\n")
        (tmp_path / "core" / "src" / "test" / "java").mkdir(parents=True)
        assert detect_project(tmp_path).test_command == "mvn -B test"


class TestAmbiguousAndUnknown:
    def test_empty_dir_unknown(self, tmp_path: Path):
        info = detect_project(tmp_path)
        assert info.language is None
        assert info.test_command is None

    def test_python_wins_over_node(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
        (tmp_path / "package.json").write_text("{}")
        assert detect_project(tmp_path).language == "python"

    def test_python_wins_over_jvm(self, maven_repo: Path):
        (maven_repo / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0"\n')
        assert detect_project(maven_repo).language == "python"

    def test_jvm_wins_over_node(self, maven_repo: Path):
        """A package.json beside a pom.xml is usually frontend assets."""
        (maven_repo / "package.json").write_text(
            json.dumps({"scripts": {"test": "jest", "build": "webpack"}})
        )
        info = detect_project(maven_repo)
        assert info.language == "jvm"
        assert info.test_command == "mvn -B test"

    def test_maven_wins_over_gradle_when_both_present(self, maven_repo: Path):
        """A stated heuristic, not a measurement: pom.xml is the more
        definitive manifest, so a project that migrated to Gradle and kept a
        stale pom.xml is read as Maven."""
        (maven_repo / "build.gradle").write_text("plugins {\n    id 'java'\n}\n")
        assert detect_project(maven_repo).build_tool == "maven"

    def test_non_jvm_project_has_no_build_tool(self, python_repo: Path, node_repo: Path):
        assert detect_project(python_repo).build_tool is None
        assert detect_project(node_repo).build_tool is None
