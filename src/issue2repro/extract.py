"""Extract reproduction signals from raw issue text.

Recognizes Python tracebacks, Node stack traces, Go panics, Rust panics,
fenced command blocks, filenames, and inline code references. Pure
functions over strings so the whole module is testable offline with
fixture payloads.
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

# --------------------------------------------------------------------------
# Go and Rust
#
# Both runtimes print their stack innermost first, and both bury the frame
# that actually failed under frames belonging to the runtime itself. The
# location line is the anchor in each case, because it is the part that is
# punctuated distinctively enough not to match prose.
# --------------------------------------------------------------------------

# Go's goroutine header: "goroutine 18 [running]:".
_GO_GOROUTINE = re.compile(r"^goroutine \d+ \[[^\]]*\]:\s*$")
# "panic: runtime error: integer divide by zero [recovered, repanicked]"
_GO_PANIC = re.compile(r"^(?P<kind>panic|fatal error):\s*(?P<message>.*?)\s*$")
# A Go frame is two lines. The location is the second and is tab indented:
# "\t/tmp/gofail/pricing/pricing.go:10 +0x124", the offset being optional.
_GO_LOCATION = re.compile(r"^\t(?P<path>\S.*?):(?P<line>\d+)(?: \+0x[0-9a-f]+)?\s*$")
# The function line above it: "example.com/shop/pricing.applyRate(...)", or
# "created by testing.(*T).Run in goroutine 1" for the frame that spawned it.
_GO_CREATED_BY = re.compile(r"^created by (?P<symbol>\S+)(?: in goroutine \d+)?\s*$")

# Go frames belonging to the runtime and the test harness rather than to the
# program. Dropping these is load bearing rather than cosmetic: a `go test`
# panic puts three of them (`testing.tRunner.func1.2`, `testing.tRunner.func1`,
# `panic`) BELOW the code that failed, so the innermost frame of every Go test
# panic ever printed is the same harness function. Compared as is, any two
# unrelated Go panics would agree on their frame.
_GO_RUNTIME_SYMBOLS = ("runtime.", "testing.", "panic", "reflect.")
_GO_RUNTIME_PATHS = ("/libexec/src/", "/goroot/", "/usr/local/go/src/", "/pkg/mod/")

# Rust 1.73 and newer put the location on the panic line and the message on
# the next one; 1.87 and newer also print a thread id in parentheses. Both
# were confirmed against rustc 1.98.0 output rather than written from memory.
_RUST_PANIC = re.compile(
    r"^thread '(?P<thread>[^']*)'(?: \(\d+\))? panicked at "
    r"(?P<path>[^\s:]\S*?):(?P<line>\d+)(?::\d+)?:\s*$"
)
# Rust before 1.73 quoted the message inline and put the location last.
_RUST_PANIC_OLD = re.compile(
    r"^thread '(?P<thread>[^']*)' panicked at '(?P<message>.*)', "
    r"(?P<path>[^\s:]\S*?):(?P<line>\d+)(?::\d+)?\s*$"
)
# A numbered backtrace frame, "   3: shop::apply_rate", and the location
# line indented under it, "             at ./src/main.rs:5:14".
_RUST_BT_FRAME = re.compile(r"^\s*(?P<index>\d+):\s+(?P<symbol>\S.*?)\s*$")
_RUST_BT_AT = re.compile(r"^\s+at (?P<path>\S+?):(?P<line>\d+)(?::\d+)?\s*$")
_RUST_BT_START = re.compile(r"^\s*stack backtrace:\s*$")

# The Rust equivalents. Frames 0 to 2 of every panic backtrace are the panic
# machinery itself and carry no location at all, so requiring a location
# already drops them; the prefixes cover a build whose std was compiled with
# debug info, and the paths cover std and third-party crates.
_RUST_RUNTIME_SYMBOLS = (
    "core::",
    "std::",
    "alloc::",
    "__rustc::",
    "rust_begin_unwind",
    "test::",
)
_RUST_RUNTIME_PATHS = ("/rustlib/", "/.cargo/registry/", "/.rustup/")

# Languages whose runtime prints the innermost frame first. Their frames are
# reversed on the way out of the extractor so that ``frames[-1]`` is the
# frame that failed for every language, which is the invariant every
# comparison downstream is written against.
_INNERMOST_FIRST = frozenset({"node", "go", "rust"})

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


def _is_rust_location(lines: list[str], i: int) -> bool:
    """True when line ``i`` is a Rust backtrace location, not a Node frame.

    Rust prints ``             at ./src/main.rs:5:14`` under each numbered
    backtrace frame, and that is indistinguishable from V8's own symbol-less
    ``at path:line:col`` if you look at the line alone. The numbered frame
    header directly above it is what tells them apart, and without this check
    a Rust panic is read as a Node stack whose innermost frame is whatever
    std function the backtrace happened to end on.
    """
    if i == 0 or not _RUST_BT_AT.match(lines[i]):
        return False
    return bool(_RUST_BT_FRAME.match(lines[i - 1].rstrip()))


def extract_node_traces(text: str) -> list[StackTrace]:
    """Find Node/V8 stack traces: an error line followed by 'at ...' frames."""
    traces: list[StackTrace] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        frame = _NODE_FRAME.match(lines[i])
        if not frame or _is_rust_location(lines, i):
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
            if not frame or _is_rust_location(lines, i):
                break
            path = frame["path"].removeprefix("file://")
            frames.append(StackFrame(path=path, line=int(frame["line"]), symbol=frame["symbol"]))
            i += 1
        traces.append(StackTrace(language="node", frames=frames, error=error))
    return traces


def outermost_first(trace: StackTrace) -> list[StackFrame]:
    """This trace's frames ordered outermost first, whatever printed them.

    Python prints a traceback outermost first; Node, Go, and Rust all print
    theirs the other way round. Every comparison downstream reads
    ``frames[-1]`` as "the frame that failed", so the normalization has to
    happen exactly once and in one place. It used to be spelled out at each
    call site, which is the drift hazard where a third language gets added
    to one of them and not the other.
    """
    if trace.language in _INNERMOST_FIRST:
        return list(reversed(trace.frames))
    return list(trace.frames)


def _bare_symbol(symbol: str, separator: str) -> str:
    """The function's own name, with its package or module path dropped.

    ``example.com/shop/pricing.applyRate`` becomes ``applyRate`` and
    ``shop::tests::checkout::{closure#0}`` becomes ``checkout``. This is the
    same rule the path side already follows by comparing basenames: the
    reporter's module path and the reproduction's need not agree (a crate
    renamed by its binary name, a module path that differs by one directory)
    while the function that failed is the same function.
    """
    while symbol.endswith("}"):
        head, brace, _ = symbol.rpartition("::{")
        if not brace:
            break
        symbol = head
    tail = symbol.rsplit(separator, 1)[-1]
    return tail or symbol


def _is_go_runtime(symbol: str | None, path: str) -> bool:
    """True for a Go frame belonging to the runtime or the test harness."""
    if any(marker in path for marker in _GO_RUNTIME_PATHS):
        return True
    if symbol is None:
        return False
    return symbol == "panic" or symbol.startswith(_GO_RUNTIME_SYMBOLS)


def _is_rust_runtime(symbol: str | None, path: str) -> bool:
    """True for a Rust frame belonging to std, the panic runtime, or a crate."""
    if any(marker in path for marker in _RUST_RUNTIME_PATHS):
        return True
    if symbol is None:
        return False
    return symbol.startswith(_RUST_RUNTIME_SYMBOLS)


def extract_go_traces(text: str) -> list[StackTrace]:
    """Find Go panics: the panic line plus the goroutine stack under it.

    Only the panicking goroutine is read. A Go panic dumps every other
    goroutine's stack too, and those are not where the failure happened, so
    reading them would put an unrelated frame last.
    """
    traces: list[StackTrace] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        panic = _GO_PANIC.match(lines[index].rstrip())
        if not panic:
            index += 1
            continue
        error = f"{panic['kind']}: {panic['message']}".rstrip(": ")
        index += 1
        # The goroutine header follows the panic line, usually after a blank
        # line. Anything else means this was prose that happened to start
        # with the word panic.
        header = index
        while header < len(lines) and not lines[header].strip():
            header += 1
        if header >= len(lines) or not _GO_GOROUTINE.match(lines[header].rstrip()):
            continue
        index = header + 1
        frames: list[StackFrame] = []
        while index < len(lines):
            location = _GO_LOCATION.match(lines[index].rstrip())
            if not location:
                # A second goroutine's header ends the stack we care about.
                if _GO_GOROUTINE.match(lines[index].rstrip()):
                    break
                if not lines[index].strip():
                    break
                index += 1
                continue
            symbol = _go_symbol(lines[index - 1].rstrip()) if index else None
            path = location["path"]
            if not _is_go_runtime(symbol, path):
                frames.append(
                    StackFrame(
                        path=path,
                        line=int(location["line"]),
                        symbol=_bare_symbol(symbol, ".") if symbol else None,
                    )
                )
            index += 1
        if frames:
            traces.append(StackTrace(language="go", frames=frames, error=error))
    return traces


def _go_symbol(line: str) -> str | None:
    """The function named on the line above a Go location line."""
    created = _GO_CREATED_BY.match(line)
    if created:
        return created["symbol"]
    if line.endswith(")"):
        head = line.rsplit("(", 1)[0]
        return head or None
    return line or None


def extract_rust_traces(text: str) -> list[StackTrace]:
    """Find Rust panics: the panic header, and a backtrace when one was set.

    The header alone locates the failure (file and line) but names no
    function, which is the common case because a bug report is usually
    pasted without ``RUST_BACKTRACE=1``. When a backtrace IS present its
    frames are used instead, because they carry the function names that make
    a frame comparison worth anything, and the header's location agrees with
    the innermost of them.
    """
    traces: list[StackTrace] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].rstrip()
        old = _RUST_PANIC_OLD.match(line)
        new = None if old else _RUST_PANIC.match(line)
        if not old and not new:
            index += 1
            continue
        if old:
            header = StackFrame(path=old["path"], line=int(old["line"]))
            message = old["message"]
            index += 1
        else:
            assert new is not None
            header = StackFrame(path=new["path"], line=int(new["line"]))
            index += 1
            message = lines[index].strip() if index < len(lines) else ""
            index += 1
        frames = _rust_backtrace(lines, index)
        traces.append(
            StackTrace(
                language="rust",
                frames=frames or [header],
                error=f"panic: {message}".rstrip(": "),
            )
        )
    return traces


def _rust_backtrace(lines: list[str], start: int) -> list[StackFrame]:
    """Frames of the backtrace that follows a panic header, innermost first.

    Returns an empty list when the panic was printed without one, which is
    what the caller falls back to the header location for.
    """
    index = start
    while index < len(lines) and not _RUST_BT_START.match(lines[index].rstrip()):
        if lines[index].strip() and not lines[index].startswith((" ", "\t")):
            return []  # ordinary output resumed; this panic carried no backtrace
        index += 1
    if index >= len(lines):
        return []
    index += 1
    frames: list[StackFrame] = []
    pending: str | None = None
    while index < len(lines):
        line = lines[index].rstrip()
        at_line = _RUST_BT_AT.match(line)
        if at_line and pending is not None:
            path = at_line["path"]
            if not _is_rust_runtime(pending, path):
                frames.append(
                    StackFrame(
                        path=path,
                        line=int(at_line["line"]),
                        symbol=_bare_symbol(pending, "::"),
                    )
                )
            pending = None
            index += 1
            continue
        frame = _RUST_BT_FRAME.match(line)
        if frame:
            pending = frame["symbol"]
            index += 1
            continue
        break
    return frames


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
        traces=(
            extract_python_traces(text)
            + extract_node_traces(text)
            + extract_go_traces(text)
            + extract_rust_traces(text)
        ),
        commands=extract_commands(text),
        filenames=extract_filenames(text),
        code_refs=extract_code_refs(text),
    )
