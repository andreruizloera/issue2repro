import json
from pathlib import Path

import pytest

from issue2repro.models import Issue, IssueRef

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str, ref: IssueRef | None = None) -> Issue:
    payload = json.loads((FIXTURES / name).read_text())
    ref = ref or IssueRef("acme", "widget", 7)
    return Issue(
        ref=ref,
        title=payload["title"],
        body=payload["body"],
        comments=[c["body"] if isinstance(c, dict) else c for c in payload.get("comments", [])],
    )


@pytest.fixture
def python_issue() -> Issue:
    return load_fixture("python_issue.json")


@pytest.fixture
def node_issue() -> Issue:
    return load_fixture("node_issue.json")


@pytest.fixture
def vague_issue() -> Issue:
    return load_fixture("vague_issue.json")


@pytest.fixture
def go_issue() -> Issue:
    """A Go panic, captured from a real `go run` under go1.27.1."""
    return load_fixture("go_issue.json")


@pytest.fixture
def rust_issue() -> Issue:
    """A Rust panic, captured from a real `cargo run` under rustc 1.98.0."""
    return load_fixture("rust_issue.json")


def load_output(name: str) -> str:
    """Raw runner output, kept verbatim in a file rather than inlined.

    These carry lines well over the line-length limit and matter only if
    they stay byte-for-byte what the toolchain printed, which is an
    argument against retyping them into a Python literal in two modules.
    """
    return (FIXTURES / name).read_text()


@pytest.fixture
def go_test_panic() -> str:
    """Real `go test ./...` output under go1.27.1.

    Carries both kinds of Go failure at once: a panic with a goroutine
    stack, and a `t.Errorf` in another package whose location line is
    deliberately not read as a frame.
    """
    return load_output("go_test_panic.txt")


@pytest.fixture
def rust_test_backtrace() -> str:
    """Real `RUST_BACKTRACE=1 cargo test` output under rustc 1.98.0."""
    return load_output("rust_test_backtrace.txt")


@pytest.fixture
def jvm_caused_by() -> str:
    """Real uncaught-exception output from `java` under OpenJDK 26.

    An `IllegalStateException` rethrown from a catch block over the
    `NullPointerException` that actually broke, so it carries a `Caused by:`
    chain and the `... 1 more` truncation the JVM prints with it.
    """
    return load_output("jvm_caused_by.txt")


@pytest.fixture
def jvm_junit_assertion() -> str:
    """Real JUnit 5 console launcher output under OpenJDK 26.

    A failed `assertEquals`, which is the case that makes the harness-frame
    filter load bearing: six frames of assertion machinery sit above the
    test method, and JUnit prints its frames with no `at` keyword.
    """
    return load_output("jvm_junit_assertion.txt")


@pytest.fixture
def python_repo(tmp_path: Path) -> Path:
    """A minimal Python project matching the python_issue fixture."""
    root = tmp_path / "pyrepo"
    (root / "src" / "mypkg").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "pyproject.toml").write_text(
        "[project]\n"
        'name = "mypkg"\n'
        'version = "0.1.0"\n'
        "dependencies = []\n\n"
        "[project.optional-dependencies]\n"
        'dev = ["pytest"]\n'
    )
    (root / "src" / "mypkg" / "parser.py").write_text("def parse(x):\n    return x\n")
    (root / "src" / "mypkg" / "decoder.py").write_text("def _decode(x):\n    return x\n")
    (root / "tests" / "test_parser.py").write_text("def test_unicode():\n    assert True\n")
    return root


@pytest.fixture
def crash_repo(tmp_path: Path) -> Path:
    """A Python project whose reproduction needs no installed dependencies.

    `verify` runs real commands, so its end-to-end tests must not depend on
    pip reaching an index. Both scripts here import nothing.
    """
    root = tmp_path / "crashrepo"
    (root / "tools").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "crashy"\nversion = "0.1.0"\ndependencies = []\n'
    )
    (root / "tools" / "repro.py").write_text(
        "def widen(token):\n"
        "    raise ValueError(\"invalid literal for int() with base 10: '-'\")\n"
        "\n\n"
        'if __name__ == "__main__":\n'
        '    widen("-")\n'
    )
    (root / "tools" / "ok.py").write_text('print("nothing to see here")\n')
    return root


def write_issue(path: Path, command: str, error: str, symbol: str = "widen") -> Path:
    """Write an issue payload whose repro step is `command`, whose traceback
    is raised in `symbol`, and whose exception line is `error`. Keeps verdict
    tests readable without a committed fixture file per case."""
    body = (
        "It blows up every time.\n\n"
        f"```bash\n{command}\n```\n\n"
        "```\n"
        "Traceback (most recent call last):\n"
        '  File "tools/repro.py", line 5, in <module>\n'
        '    widen("-")\n'
        f'  File "tools/repro.py", line 2, in {symbol}\n'
        "    raise ValueError(...)\n"
        f"{error}\n"
        "```\n"
    )
    path.write_text(json.dumps({"title": "crash on widen()", "body": body, "comments": []}))
    return path


@pytest.fixture
def unittest_repo(tmp_path: Path) -> Path:
    """A project whose failing test runs under unittest rather than pytest.

    unittest is in the standard library, so this reproduction installs
    nothing and the end-to-end test stays offline like the others.
    """
    root = tmp_path / "unittestrepo"
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "widen"\nversion = "0.1.0"\ndependencies = []\n'
    )
    (root / "test_widen.py").write_text(
        "import unittest\n"
        "\n"
        "\n"
        "def widen(token):\n"
        "    raise ValueError(\"invalid literal for int() with base 10: '-'\")\n"
        "\n"
        "\n"
        "class WidenTest(unittest.TestCase):\n"
        "    def test_widen(self):\n"
        '        widen("-")\n'
    )
    return root


def write_unittest_issue(path: Path) -> Path:
    """An issue reporting the unittest_repo failure, in unittest's own format."""
    body = (
        "The suite errors out on a clean checkout.\n\n"
        "```bash\npython3 -m unittest discover\n```\n\n"
        "```\n"
        "ERROR: test_widen (test_widen.WidenTest.test_widen)\n"
        "----------------------------------------------------------------------\n"
        "Traceback (most recent call last):\n"
        '  File "test_widen.py", line 10, in test_widen\n'
        '    widen("-")\n'
        '  File "test_widen.py", line 5, in widen\n'
        "    raise ValueError(...)\n"
        "ValueError: invalid literal for int() with base 10: '-'\n"
        "```\n"
    )
    path.write_text(json.dumps({"title": "widen() errors out", "body": body, "comments": []}))
    return path


@pytest.fixture
def node_repo(tmp_path: Path) -> Path:
    """A minimal Node project matching the node_issue fixture."""
    root = tmp_path / "noderepo"
    (root / "src").mkdir(parents=True)
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "app",
                "version": "1.0.0",
                "scripts": {"test": "node --test", "build": "tsc"},
            }
        )
    )
    (root / "src" / "config.js").write_text("module.exports = {};\n")
    (root / "src" / "index.js").write_text("require('./config');\n")
    return root
