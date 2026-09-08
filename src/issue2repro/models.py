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

    language: str  # "python", "node", "go", or "rust"
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

    language: str | None = None  # "python", "node", "jvm", or None
    manifests: list[str] = field(default_factory=list)
    install_commands: list[str] = field(default_factory=list)
    test_command: str | None = None
    build_command: str | None = None
    # Which build tool drives a "jvm" project: "maven" or "gradle". None for
    # every other language, where the language already names the tool.
    build_tool: str | None = None


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
class Step:
    """One command in reproduce.sh, with the role it plays.

    ``kind`` is "setup" (bring the environment up) or "repro" (exercise the
    reported bug). The split is what lets `verify` tell an install failure
    apart from the failure the issue describes.
    """

    index: int
    kind: str
    command: str


@dataclass
class FailureSignature:
    """What a failure looks like: an exception, the frames it came from, and
    the tests it broke.

    Used for both the signature the issue describes (expected) and the one
    a run actually produced (observed).

    ``frames`` is always stored the way Python prints a traceback, outermost
    first, so ``frames[-1]`` is the frame that raised. Node, Go, and Rust all
    print their stacks the other way round and are reversed on the way in by
    :func:`issue2repro.extract.outermost_first`, so the last element means
    the same thing for every language.
    """

    exception_type: str | None = None
    exception_message: str | None = None
    message_truncated: bool = False
    frames: list[StackFrame] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return self.exception_type is None and not self.tests

    def describe(self) -> str:
        parts = []
        if self.exception_type:
            message = self.exception_message or ""
            suffix = "..." if self.message_truncated else ""
            parts.append(
                f"{self.exception_type}: {message}{suffix}" if message else self.exception_type
            )
        if self.tests:
            parts.append(", ".join(self.tests[:3]))
        return "; ".join(parts) if parts else "nothing checkable"


@dataclass
class SignatureMatch:
    """The per-component comparison of an expected and an observed signature.

    Each component is "match", "mismatch", or "unknown"; "unknown" means
    there was nothing on one side to compare, which is never evidence.
    """

    exception: str = "unknown"
    message: str = "unknown"
    frames: str = "unknown"
    tests: str = "unknown"
    matched_tests: list[str] = field(default_factory=list)
    matched_frame: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def components(self) -> tuple[str, str, str, str]:
        return (self.exception, self.message, self.frames, self.tests)


@dataclass
class Verification:
    """The result of running a workspace and comparing signatures."""

    verdict: str
    where: str
    exit_code: int
    expected: FailureSignature
    observed: FailureSignature
    match: SignatureMatch
    failed_step: Step | None = None
    reason: str = ""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "where": self.where,
            "exit_code": self.exit_code,
            "reason": self.reason,
            "failed_step": asdict(self.failed_step) if self.failed_step else None,
            "expected": asdict(self.expected),
            "observed": asdict(self.observed),
            "match": asdict(self.match),
        }


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
