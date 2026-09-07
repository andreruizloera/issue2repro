"""Extract reproduction signals from raw issue text.

Recognizes Python tracebacks, Node stack traces, fenced command blocks,
filenames, and inline code references. Pure functions over strings so the
whole module is testable offline with fixture payloads.
"""

from __future__ import annotations

import re

from issue2repro.models import Signals, StackFrame, StackTrace

_PY_TRACEBACK_START = re.compile(r"^\s*Traceback \(most recent call last\):\s*$")
_PY_FRAME = re.compile(r'^\s*File "(?P<path>[^"]+)", line (?P<line>\d+)(?:, in (?P<symbol>\S+))?')
_PY_ERROR = re.compile(
    r"^(?P<error>[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|StopIteration)\b.*"
    r"|[A-Za-z_][\w.]*: .+)$"
)

_NODE_FRAME = re.compile(
    r"^\s*at\s+(?:(?P<symbol>[^\s(]+(?:\s\[as\s[^\]]+\])?)\s+\()?"
    r"(?P<path>(?:file://)?(?:node:)?[^():\s][^():]*?):(?P<line>\d+):\d+\)?\s*$"
)
_NODE_ERROR = re.compile(
    r"^\s*(?:Uncaught\s+)?(?P<error>\w*(?:Error|Exception)\b.*?:.*|\w*(?:Error|Exception)\b.*)\s*$"
)

_FENCE = re.compile(r"^```(?P<lang>[\w+-]*)\s*$")

_COMMAND_LANGS = {"bash", "sh", "shell", "zsh", "console", "terminal", ""}
_COMMAND_HEADS = {
    "python",
    "python3",
    "pip",
    "pip3",
    "pytest",
    "tox",
    "uv",
    "poetry",
    "node",
    "npm",
    "npx",
    "yarn",
    "pnpm",
    "make",
    "git",
    "cd",
    "bash",
    "sh",
    "docker",
    "cargo",
    "go",
    "ruby",
    "bundle",
    "gradle",
    "mvn",
    "export",
    "env",
}

_FILENAME = re.compile(
    r"(?<![\w/])(?P<name>[\w][\w./-]*\.(?:py|pyi|js|mjs|cjs|jsx|ts|tsx|json|toml|cfg|ini|txt|ya?ml|sh|rs|go|c|h|cpp|java|rb))(?![\w.])"
)
_INLINE_CODE = re.compile(r"`([^`\n]+)`")


def _split_fences(text: str) -> tuple[list[tuple[str, str]], str]:
    """Return (fenced blocks as (lang, content), text outside fences)."""
    blocks: list[tuple[str, str]] = []
    outside: list[str] = []
    lang: str | None = None
    buf: list[str] = []
    for line in text.splitlines():
        fence = _FENCE.match(line)
        if fence and lang is None:
            lang = fence["lang"].lower()
            buf = []
        elif line.strip().startswith("```") and lang is not None:
            blocks.append((lang, "\n".join(buf)))
            lang = None
        elif lang is not None:
            buf.append(line)
        else:
            outside.append(line)
    if lang is not None:  # unterminated fence: keep its content visible
        outside.extend(buf)
    return blocks, "\n".join(outside)


def extract_python_traces(text: str) -> list[StackTrace]:
    """Find Python tracebacks: frame list plus the final exception line."""
    traces: list[StackTrace] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if not _PY_TRACEBACK_START.match(lines[i]):
            i += 1
            continue
        frames: list[StackFrame] = []
        error: str | None = None
        i += 1
        while i < len(lines):
            frame = _PY_FRAME.match(lines[i])
            if frame:
                frames.append(
                    StackFrame(
                        path=frame["path"],
                        line=int(frame["line"]),
                        symbol=frame["symbol"],
                    )
                )
                i += 1
                # Skip whatever the interpreter echoed under the frame: the
                # source line, and on 3.11 and later the "~~~^^^" anchor
                # under it. Anything indented that is neither another frame
                # nor the exception line belongs to this frame.
                while (
                    i < len(lines)
                    and lines[i].startswith((" ", "\t"))
                    and not _PY_FRAME.match(lines[i])
                    and not _PY_ERROR.match(lines[i].strip())
                ):
                    i += 1
                continue
            err = _PY_ERROR.match(lines[i].strip())
            if err:
                error = err["error"].strip()
                i += 1
            break
        if frames:
            traces.append(StackTrace(language="python", frames=frames, error=error))
    return traces


def extract_node_traces(text: str) -> list[StackTrace]:
    """Find Node/V8 stack traces: an error line followed by 'at ...' frames."""
    traces: list[StackTrace] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        frame = _NODE_FRAME.match(lines[i])
        if not frame:
            i += 1
            continue
        error: str | None = None
        for back in range(i - 1, max(i - 4, -1), -1):
            err = _NODE_ERROR.match(lines[back])
            if err:
                error = err["error"].strip()
                break
            if lines[back].strip():
                break
        frames: list[StackFrame] = []
        while i < len(lines):
            frame = _NODE_FRAME.match(lines[i])
            if not frame:
                break
            path = frame["path"].removeprefix("file://")
            frames.append(StackFrame(path=path, line=int(frame["line"]), symbol=frame["symbol"]))
            i += 1
        traces.append(StackTrace(language="node", frames=frames, error=error))
    return traces


def _looks_like_command(line: str) -> bool:
    parts = line.split()
    if not parts:
        return False
    head = parts[0]
    if "=" in head and len(parts) > 1:  # VAR=value prefix before a command
        return parts[1].split("=", 1)[0] in _COMMAND_HEADS if "=" not in parts[1] else False
    return head in _COMMAND_HEADS


def extract_commands(text: str) -> list[str]:
    """Pull runnable commands out of fenced shell blocks.

    In console-style blocks only '$ '-prefixed lines count; in bash/sh
    blocks every non-comment line counts. Untagged blocks contribute only
    lines whose first word is a known command.
    """
    blocks, _ = _split_fences(text)
    commands: list[str] = []
    for lang, content in blocks:
        if lang not in _COMMAND_LANGS:
            continue
        for raw in content.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("$ "):
                commands.append(line[2:].strip())
                continue
            is_shell_block = lang in {"bash", "sh", "shell", "zsh"}
            if is_shell_block or _looks_like_command(line):
                commands.append(line)
    seen: set[str] = set()
    unique = []
    for cmd in commands:
        if cmd not in seen:
            seen.add(cmd)
            unique.append(cmd)
    return unique


def extract_filenames(text: str) -> list[str]:
    """Collect filename-looking tokens, excluding URLs."""
    names: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        if "http://" in line or "https://" in line:
            line = re.sub(r"https?://\S+", "", line)
        for match in _FILENAME.finditer(line):
            name = match["name"]
            if name not in seen:
                seen.add(name)
                names.append(name)
    return names


def extract_code_refs(text: str) -> list[str]:
    """Inline-code references that name symbols rather than files or commands."""
    _, outside = _split_fences(text)
    refs: list[str] = []
    seen: set[str] = set()
    for token in _INLINE_CODE.findall(outside):
        token = token.strip()
        if not token or _FILENAME.fullmatch(token) or _looks_like_command(token):
            continue
        if len(token) <= 80 and token not in seen:
            seen.add(token)
            refs.append(token)
    return refs


def extract_signals(text: str) -> Signals:
    """Run every extractor over the combined issue text."""
    return Signals(
        traces=extract_python_traces(text) + extract_node_traces(text),
        commands=extract_commands(text),
        filenames=extract_filenames(text),
        code_refs=extract_code_refs(text),
    )
