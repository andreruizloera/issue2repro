"""Data structures shared across the pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class IssueRef:
    """A parsed GitHub issue reference."""

    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def issue_url(self) -> str:
        return f"{self.repo_url}/issues/{self.number}"


@dataclass
class Issue:
    """Issue content: title, body, and comment bodies."""

    ref: IssueRef
    title: str
    body: str
    comments: list[str] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        """Title, body, and comments joined for signal extraction."""
        parts = [self.title, self.body, *self.comments]
        return "\n\n".join(p for p in parts if p)


@dataclass
class StackFrame:
    """One frame of a stack trace."""

    path: str
    line: int
    symbol: str | None = None


@dataclass
class StackTrace:
    """A recognized stack trace from the issue text."""

    language: str  # "python" or "node"
    frames: list[StackFrame]
    error: str | None = None


@dataclass
class Signals:
    """Everything extracted from the issue text."""

    traces: list[StackTrace] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    filenames: list[str] = field(default_factory=list)
    code_refs: list[str] = field(default_factory=list)


@dataclass
class ProjectInfo:
    """What we inferred from the cloned repository itself."""

    language: str | None = None  # "python", "node", or None
    manifests: list[str] = field(default_factory=list)
    install_commands: list[str] = field(default_factory=list)
    test_command: str | None = None
    build_command: str | None = None


@dataclass
class ConfidenceComponent:
    """One weighted piece of the confidence score."""

    name: str
    weight: int
    earned: int
    reason: str


@dataclass
class ConfidenceReport:
    """The full confidence breakdown."""

    components: list[ConfidenceComponent] = field(default_factory=list)

    @property
    def score(self) -> int:
        total_weight = sum(c.weight for c in self.components)
        if total_weight == 0:
            return 0
        earned = sum(c.earned for c in self.components)
        return round(100 * earned / total_weight)


@dataclass
class Analysis:
    """The complete result of analyzing one issue against its repository."""

    issue: Issue
    signals: Signals
    project: ProjectInfo
    confidence: ConfidenceReport

    def to_metadata(self) -> dict[str, Any]:
        """Structured dict for metadata.json."""
        return {
            "issue": {
                "owner": self.issue.ref.owner,
                "repo": self.issue.ref.repo,
                "number": self.issue.ref.number,
                "url": self.issue.ref.issue_url,
                "title": self.issue.title,
            },
            "signals": asdict(self.signals),
            "project": asdict(self.project),
            "confidence": {
                "score": self.confidence.score,
                "components": [asdict(c) for c in self.confidence.components],
            },
        }
