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
