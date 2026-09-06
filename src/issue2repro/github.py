"""GitHub issue fetching: URL parsing, gh CLI access, and fixture loading."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

from issue2repro.models import Issue, IssueRef


class Issue2ReproError(Exception):
    """Expected failure with a clean, user-facing message.

    ``exit_code`` lets a caller distinguish "this went wrong" (1) from
    "issue2repro could not tell" (2), which `verify` relies on: a missing
    Docker is not a failed verification.
    """

    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


_URL_RE = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9._-]+?)(?:\.git)?/"
    r"issues/(?P<number>\d+)"
    r"(?:[/#?].*)?$"
)
_SHORT_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)/"
    r"(?P<repo>[A-Za-z0-9._-]+)#(?P<number>\d+)$"
)


def parse_issue_url(url: str) -> IssueRef:
    """Parse a GitHub issue URL or an owner/repo#number shorthand.

    Raises Issue2ReproError with a clear message on anything else.
    """
    for pattern in (_URL_RE, _SHORT_RE):
        match = pattern.match(url.strip())
        if match:
            return IssueRef(
                owner=match["owner"],
                repo=match["repo"],
                number=int(match["number"]),
            )
    raise Issue2ReproError(
        f"could not parse {url!r} as a GitHub issue.\n"
        "Expected https://github.com/OWNER/REPO/issues/NUMBER or OWNER/REPO#NUMBER."
    )


def gh_available() -> bool:
    """True when the gh CLI exists and is authenticated."""
    if shutil.which("gh") is None:
        return False
    result = subprocess.run(
        ["gh", "auth", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0


def _gh_api(endpoint: str) -> object:
    result = subprocess.run(
        ["gh", "api", endpoint],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise Issue2ReproError(
            f"gh api {endpoint} failed: {detail[-1] if detail else 'unknown error'}"
        )
    return json.loads(result.stdout)


def fetch_issue(ref: IssueRef) -> Issue:
    """Fetch title, body, and comments through the gh CLI.

    Degrades with a clear message when gh is missing or unauthenticated.
    """
    if shutil.which("gh") is None:
        raise Issue2ReproError(
            "the gh CLI is not installed, so the issue cannot be fetched.\n"
            "Install it from https://cli.github.com/ and run 'gh auth login',\n"
            "or pass --issue-file with a saved issue payload."
        )
    if not gh_available():
        raise Issue2ReproError(
            "the gh CLI is installed but not authenticated.\n"
            "Run 'gh auth login', or pass --issue-file with a saved issue payload."
        )
    base = f"repos/{ref.owner}/{ref.repo}/issues/{ref.number}"
    payload = _gh_api(base)
    if not isinstance(payload, dict):
        raise Issue2ReproError(f"unexpected response shape from gh api {base}")
    comments_payload = _gh_api(f"{base}/comments")
    comments = (
        [c.get("body", "") for c in comments_payload if isinstance(c, dict)]
        if isinstance(comments_payload, list)
        else []
    )
    return Issue(
        ref=ref,
        title=payload.get("title", "") or "",
        body=payload.get("body", "") or "",
        comments=[c for c in comments if c],
    )


def load_issue_file(ref: IssueRef, path: Path) -> Issue:
    """Load an issue payload from a local JSON file (offline mode).

    Expected shape: {"title": str, "body": str, "comments": [{"body": str} | str, ...]}.
    """
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise Issue2ReproError(f"issue file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise Issue2ReproError(f"issue file {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise Issue2ReproError(f"issue file {path} must contain a JSON object")
    comments: list[str] = []
    for entry in payload.get("comments", []):
        if isinstance(entry, str):
            comments.append(entry)
        elif isinstance(entry, dict) and entry.get("body"):
            comments.append(str(entry["body"]))
    return Issue(
        ref=ref,
        title=str(payload.get("title", "")),
        body=str(payload.get("body", "")),
        comments=comments,
    )


def clone_repo(clone_url: str, dest: Path) -> None:
    """Shallow-clone a repository. Accepts https and file:// URLs."""
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--quiet", clone_url, str(dest)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        raise Issue2ReproError(
            f"git clone of {clone_url} failed: {detail[-1] if detail else 'unknown error'}"
        )
