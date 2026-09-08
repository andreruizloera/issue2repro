"""Failure signatures: what the issue says broke, what a run actually did.

Pure functions over strings and Signals, so the whole comparison is
testable offline without running anything. The module answers three
questions and nothing else:

- what failure does the issue describe (:func:`expected_signature`)
- what failure did this output show (:func:`observed_signature`)
- do they match, component by component (:func:`compare_signatures`)

Everything here is a heuristic over text, and it is labeled as one: a
"match" means the exception type, the frame it was raised in, and the
failing test line up, not that the two runs executed the same bytecode.
"""

from __future__ import annotations

import re

from issue2repro.extract import (
    extract_go_traces,
    extract_jvm_traces,
    extract_node_traces,
    extract_python_traces,
    extract_rust_traces,
    outermost_first,
)
from issue2repro.models import (
    FailureSignature,
    Signals,
    SignatureMatch,
    StackFrame,
    StackTrace,
)

# An exception line, as printed by Python, Node, or pytest's "E   " echo.
# The type name must end in a recognizable suffix, which keeps ordinary
# prose with a colon in it from being read as an exception.
_EXC_SUFFIXES = r"(?:Error|Exception|Warning|Interrupt|Exit|StopIteration|Failure|Failed)"
_EXC_LINE = re.compile(rf"^(?P<type>[A-Za-z_][\w.]*{_EXC_SUFFIXES})(?::\s*(?P<message>.*))?$")

# Go and Rust have no exception type to name. Both call the failure a panic,
# so "panic" is what this tool records as the type, and everything that
# distinguishes one panic from another lives in the message. A colon and a
# message are required, so the bare word in prose is not read as a failure.
_PANIC_LINE = re.compile(r"^(?P<type>panic|fatal error):\s*(?P<message>\S.*)$")

# pytest's short summary: "FAILED tests/test_x.py::test_y - ValueError: boom"
_PYTEST_SUMMARY = re.compile(
    r"^(?:FAILED|ERROR)\s+(?P<nodeid>[^\s]+?::[^\s]+|[^\s]+\.py)(?:\s+-\s+(?P<detail>.*))?$"
)
# pytest echoes the failing assertion/exception under "E   " in the failure body.
_PYTEST_E_LINE = re.compile(r"^E\s{2,}(?P<body>.+)$")

# A pytest node id written by hand in an issue: "tests/test_x.py::test_y".
_NODE_ID = re.compile(r"(?P<nodeid>[\w./-]+\.py::[\w\[\]:.\-]+)")

_TEST_NAME = re.compile(r"^test[\w]*$")

# Per-test failure lines from the runners that are not pytest. Each one is
# anchored hard enough that ordinary prose does not trip it: unittest wants
# a method whose name starts with "test", go and cargo want their own
# decorations, and node's TAP output wants a numbered "not ok".
#   unittest:  "ERROR: test_widen (test_widen.WidenTest.test_widen)"
#   go test:   "--- FAIL: TestDivide/by_zero (0.00s)"
#   cargo:     "---- tests::divides_by_zero stdout ----"
#   node TAP:  "not ok 1 - applies a discount"
#   node spec: "  x applies a discount (1.2ms)"
_UNITTEST_FAIL = re.compile(
    r"^(?:FAIL|ERROR):\s+(?P<name>test\w*)(?:\s+\((?P<context>[\w.]+)\))?\s*$"
)
_GO_FAIL = re.compile(r"^\s*--- FAIL:\s+(?P<name>\S+)\s+\([\d.]+m?s\)\s*$")
_CARGO_FAIL = re.compile(r"^----\s+(?P<name>\S+)\s+stdout\s+----\s*$")
_NODE_TAP_FAIL = re.compile(r"^\s*not ok \d+ - (?P<name>.+?)\s*$")
# The JUnit console launcher's "Failures (1):" block, which it prints in
# every --details mode:
#   "  JUnit Jupiter:PricingTest:unknownCouponIsIgnored()"
# The engine name is the anchor. Without it this would match any indented
# colon-separated word ending in parentheses, which is a shape ordinary
# prose reaches too easily.
_JUNIT_FAIL = re.compile(
    r"^\s+JUnit \w+:(?P<cls>[\w.$]+):(?P<name>[\w$]+)\((?P<params>[^)]*)\)\s*$"
)
# Maven Surefire, which prints a line per failing test:
#   "[ERROR] com.example.shop.PricingTest.unknownCouponIsIgnored -- Time
#    elapsed: 0.003 s <<< ERROR!"
#   "[ERROR] com.example.shop.ShippingTest.flatRateUnderThreshold(int)[1]
#    -- Time elapsed: 0.067 s <<< FAILURE!"
# Surefire calls an assertion failure FAILURE and any other exception ERROR;
# both are failing tests here. The parameter signature and the parametrized
# index are dropped so the id reduces to a bare method name the way every
# other runner's does.
#
# THE CLASS-LEVEL LINE IS THE HAZARD and it is why " -- " is in the pattern
# rather than just "Time elapsed". Surefire prints
#   "[ERROR] Tests run: 3, Failures: 3, ..., Time elapsed: 0.182 s <<<
#    FAILURE! -- in com.example.shop.ShippingTest"
# for the class as a whole, which also ends in "<<< FAILURE!" and would
# otherwise be recorded as a test named after a count. It is excluded twice
# over: it separates the name from "Time elapsed" with a comma rather than
# " -- ", and "Tests run: 3" is not a dotted Java identifier.
_SUREFIRE_FAIL = re.compile(
    r"^\[ERROR\]\s+(?P<cls>[\w$.]+)\.(?P<name>[\w$]+)"
    r"(?:\([^)]*\))?(?:\[[^\]]*\])?"
    r"\s+--\s+Time elapsed:.*<<<\s+(?:FAILURE|ERROR)!\s*$"
)
# Surefire's end-of-run summary, which repeats each failure more compactly:
#   "[ERROR]   ShippingTest.freeOverFiftyDollars:13 expected: <0> but was: <599>"
#   "[ERROR]   PricingTest.unknownCouponIsIgnored:22 » NullPointer Cannot ..."
# This is redundant with the per-test lines in a full log and is read anyway
# because an issue reporter usually pastes the TAIL of a failed build, which
# is this block and not the per-test lines hundreds of lines above it. Both
# forms reduce to the same id, so a full log dedups to one entry.
_SUREFIRE_SUMMARY_FAIL = re.compile(
    r"^\[ERROR\]\s+(?P<cls>[\w$]+)\.(?P<name>[\w$]+)"
    r"(?:\([^)]*\))?(?:\[[^\]]*\])?:\d+\s+\S"
)
# Gradle prints "Class > method() FAILED", and nests deeper for @Nested
# classes and for a parametrized case's display name:
#   "PricingTest > unknownCouponIsIgnored() FAILED"
#   "ShippingTest > flatRateUnderThreshold(int) > [1] 100 FAILED"
# Parsed by splitting on " > " rather than with one regex, because the
# method is not at a fixed depth: see _gradle_test_id.
_GRADLE_FAILED = re.compile(r"^(?P<body>\S.*?)\s+FAILED\s*$")
_GRADLE_METHOD = re.compile(r"^(?P<name>[\w$]+)\([^)]*\)$")
# The line Gradle indents under a failing test, which carries the exception
# type and a location but no message and no stack:
#   "    java.lang.NullPointerException at PricingTest.java:22"
# Read only when it directly follows a Gradle FAILED line, because on its
# own this shape is close to prose.
_GRADLE_EXC = re.compile(
    rf"^\s+(?P<type>[A-Za-z_][\w.]*{_EXC_SUFFIXES})\s+at\s+[\w$.]+\.\w+:\d+\s*$"
)
_NODE_SPEC_FAIL = re.compile(r"^\s*[✖✗×]\s+(?P<name>.+?)\s*\([\d.]+m?s\)\s*$")
# A TAP line can announce a skipped or planned test rather than a failure.
_TAP_DIRECTIVE = re.compile(r"#\s*(SKIP|TODO)\b", re.IGNORECASE)

# pytest prints no "File ..., line N, in fn" frames. It locates each frame on
# a line of its own at the end of that frame's block:
#   "tests/test_evaluate.py:13:"            (the default long traceback)
#   "src/tinycalc/evaluate.py:23: ValueError"  (the last frame, with the type)
#   "tests/test_evaluate.py:13: in test_x"     (--tb=short and collection errors)
# The long form names no function, so the function is read from the "def"
# line pytest echoed above the location.
_PYTEST_FRAME_IN = re.compile(
    r"^(?P<path>[\w./\\+-]+\.\w+):(?P<line>\d+): in (?P<symbol>[\w.<>]+)$"
)
_PYTEST_FRAME_AT = re.compile(r"^(?P<path>[\w./\\+-]+\.\w+):(?P<line>\d+):(?:\s+[A-Za-z_][\w.]*)?$")
_DEF_LINE = re.compile(r"^\s*(?:async\s+)?def\s+(?P<symbol>\w+)\s*\(")

# pytest truncates the short-summary detail with a trailing ellipsis.
_ELLIPSIS = "..."

_WHITESPACE = re.compile(r"\s+")


def _split_exception(text: str) -> tuple[str | None, str | None]:
    """Split 'ValueError: boom' into ('ValueError', 'boom').

    Also splits a Go or Rust panic line, which names no type of its own and
    is recorded under the type "panic" or "fatal error".
    """
    stripped = text.strip()
    match = _EXC_LINE.match(stripped) or _PANIC_LINE.match(stripped)
    if not match:
        return None, None
    message = match["message"]
    return match["type"], message.strip() if message else None


def _test_name(nodeid: str) -> str:
    """The bare test function name from a node id, parametrization stripped."""
    tail = nodeid.rsplit("::", 1)[-1]
    return tail.split("[", 1)[0]


def _unittest_id(name: str, context: str | None) -> str:
    """A unittest failure header as one id: 'test_x.Case::test_method'.

    unittest names a test twice, and differently by version: 3.11 and later
    print "test_widen (test_widen.WidenTest.test_widen)" where 3.10 printed
    "test_widen (test_widen.WidenTest)". Normalizing both to one id with
    "::" before the method means every runner's ids reduce to a bare test
    name under the same rule, and the same line in an issue and in a run
    produces the same string.
    """
    if not context:
        return name
    if context.endswith("." + name):
        context = context[: -len(name) - 1]
    return f"{context}::{name}" if context else name


def _gradle_test_id(line: str) -> str | None:
    """'ShippingTest > flatRateUnderThreshold(int) > [1] 100 FAILED' -> id.

    Gradle does not put the method at a fixed depth. A plain test is
    ``Class > method()``, a parametrized case appends the display name as a
    third segment (``> [1] 100``), and an ``@Nested`` class adds one segment
    per level. So the segments are scanned for the LAST one shaped like a
    method, which is the only segment that carries parentheses, and the
    segment before it is the class.

    Returning None for a line with no method-shaped segment is what keeps
    Gradle's own progress output from being read as a test: ``> Task :test
    FAILED`` ends in FAILED too, and has no parenthesized segment.
    """
    failed = _GRADLE_FAILED.match(line)
    if not failed:
        return None
    segments = [part.strip() for part in failed["body"].split(" > ")]
    for index in range(len(segments) - 1, -1, -1):
        method = _GRADLE_METHOD.match(segments[index])
        if not method:
            continue
        if index == 0:
            return method["name"]
        return f"{segments[index - 1]}::{method['name']}"
    return None


def gradle_exception(output: str) -> tuple[str, None] | None:
    """The exception type Gradle names under a failing test, if any.

    Gradle's default test output prints no stack trace at all. It prints
    one indented line per failure carrying the type and a source location:
    ``java.lang.NullPointerException at PricingTest.java:22``. That line is
    invisible to the general exception scanner, which requires the type to
    end the line or be followed by ``: message``, so without this a Gradle
    run produces a completely empty signature.

    Only the type is returned. Gradle prints no message, and the message is
    left None rather than filled in from the location, so the comparison
    reports ``unknown`` for it instead of a wrong answer. The location is
    deliberately NOT turned into a frame: it names no symbol, which is the
    same reason a Rust panic without ``RUST_BACKTRACE`` compares as unknown.

    The anchor is position, not shape: the line must directly follow a
    Gradle FAILED line.
    """
    lines = output.splitlines()
    for index, line in enumerate(lines[:-1]):
        if _gradle_test_id(line) is None:
            continue
        found = _GRADLE_EXC.match(lines[index + 1])
        if found:
            return found["type"], None
    return None


def runner_tests(text: str) -> tuple[list[str], str | None]:
    """Failing test names printed by a runner other than pytest.

    unittest, go test, cargo test, node --test, the JUnit console launcher,
    Maven Surefire, and Gradle each report failures in their own format. One
    run means one runner, so the first format that matches anything wins
    rather than merging two runners' names.

    The three JVM formats all record a test as ``Class::method`` with the
    SIMPLE class name, even though Surefire prints a fully qualified one.
    That is deliberate: the same failure pasted from any of the three then
    reduces to the same id, so an issue quoting a Gradle log still compares
    against a run driven by Surefire.
    """
    unittest_names: list[str] = []
    go_names: list[str] = []
    cargo_names: list[str] = []
    node_names: list[str] = []
    junit_names: list[str] = []
    surefire_names: list[str] = []
    gradle_names: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        found = _UNITTEST_FAIL.match(line)
        if found:
            unittest_names.append(_unittest_id(found["name"], found["context"]))
            continue
        found = _GO_FAIL.match(line)
        if found:
            go_names.append(found["name"])
            continue
        found = _CARGO_FAIL.match(line)
        if found:
            cargo_names.append(found["name"])
            continue
        found = _JUNIT_FAIL.match(line)
        if found:
            # Recorded as "Class::method" so the bare method name is what
            # _test_name() returns, matching how every other runner's id
            # reduces for comparison.
            junit_names.append(f"{found['cls']}::{found['name']}")
            continue
        found = _SUREFIRE_FAIL.match(line) or _SUREFIRE_SUMMARY_FAIL.match(line)
        if found:
            # Surefire's per-test line is fully qualified and its summary
            # line is not; recording the simple name collapses the two.
            simple = found["cls"].rsplit(".", 1)[-1]
            surefire_names.append(f"{simple}::{found['name']}")
            continue
        gradle_id = _gradle_test_id(line)
        if gradle_id:
            gradle_names.append(gradle_id)
            continue
        found = _NODE_TAP_FAIL.match(line) or _NODE_SPEC_FAIL.match(line)
        if found and not _TAP_DIRECTIVE.search(line):
            node_names.append(found["name"])
    for names, source in (
        (unittest_names, "unittest failure lines"),
        (go_names, "go test failure lines"),
        (cargo_names, "cargo test failure lines"),
        (junit_names, "JUnit failure lines"),
        (surefire_names, "Maven Surefire failure lines"),
        (gradle_names, "Gradle failure lines"),
        (node_names, "node --test failure lines"),
    ):
        unique = list(dict.fromkeys(names))
        if unique:
            return unique, source
    return [], None


def _basename(path: str) -> str:
    """The file name, with every directory prefix dropped."""
    return path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]


def _is_pytest_rule(line: str) -> bool:
    """True for a pytest section rule: '=== FAILURES ===', '__ test_x __', '_ _ _'."""
    stripped = line.strip()
    if len(stripped) < 3:
        return False
    body = stripped.replace(" ", "")
    if len(body) >= 3 and all(char in "_=" for char in body):
        return True
    return bool(re.match(r"^[_=]{3,}.*[_=]{3,}$", stripped))


def _first_def(lines: list[str]) -> str | None:
    """The function pytest echoed for a frame: the first 'def' of the block.

    The first one, not the last: pytest prints the frame's own function and
    then its body, so a nested def further down belongs to the body, not to
    the frame.
    """
    for line in lines:
        match = _DEF_LINE.match(line)
        if match:
            return match["symbol"]
    return None


def pytest_frames(output: str) -> list[StackFrame]:
    """Frames from pytest's failure body, outermost first.

    With more than one failing test the blocks concatenate, so the last frame
    is the innermost frame of the last reported failure. That is the same
    "last one wins" rule the exception line already follows.
    """
    lines = output.splitlines()
    frames: list[StackFrame] = []
    block_start = 0
    for index, raw in enumerate(lines):
        line = raw.rstrip()
        if _is_pytest_rule(line):
            block_start = index + 1
            continue
        located = _PYTEST_FRAME_IN.match(line)
        symbol = located["symbol"] if located else None
        if located is None:
            located = _PYTEST_FRAME_AT.match(line)
            if located is None:
                continue
            symbol = _first_def(lines[block_start:index])
        frames.append(StackFrame(path=located["path"], line=int(located["line"]), symbol=symbol))
        block_start = index + 1
    return frames


def native_traces(output: str) -> list[StackTrace]:
    """Go and Rust panics and JVM exceptions, in the order they appear."""
    return extract_go_traces(output) + extract_rust_traces(output) + extract_jvm_traces(output)


def _trace_source(language: str) -> str:
    """How to name a trace from this language in the evidence list.

    A JVM exception is not a panic, and calling it one in the output a
    reader checks the verdict against would be a small lie of exactly the
    kind the packet's "Reported locations" rename avoided in bugpacket.
    """
    noun = "exception" if language == "jvm" else "panic"
    return f"{language} {noun}"


def observed_frames(output: str) -> tuple[list[StackFrame], str | None]:
    """The frames this run's output shows, with the source that produced them.

    pytest's failure body first, since a pytest run is the case the tool
    generates most often; then a plain Python traceback; then a Node stack;
    then a Go or Rust panic or a JVM exception. Every one of those but
    pytest and Python prints innermost first, and :func:`outermost_first`
    normalizes them so that ``frames[-1]`` means the same thing everywhere.
    """
    frames = pytest_frames(output)
    if frames:
        return frames, "pytest traceback frames"
    python = [trace for trace in extract_python_traces(output) if trace.frames]
    if python:
        return python[-1].frames, "traceback frames in the output"
    node = [trace for trace in extract_node_traces(output) if trace.frames]
    if node:
        return outermost_first(node[-1]), "node stack frames in the output"
    native = [trace for trace in native_traces(output) if trace.frames]
    if native:
        last = native[-1]
        return outermost_first(last), f"{_trace_source(last.language)} frames in the output"
    return [], None


def describe_frame(frame: StackFrame) -> str:
    """One frame as a person reads it: 'evaluate (src/tinycalc/evaluate.py)'."""
    return f"{frame.symbol or 'an unnamed function'} ({frame.path})"


def compare_frames(
    expected: list[StackFrame], observed: list[StackFrame]
) -> tuple[str, str, str | None]:
    """Compare where the exception was raised: match, mismatch, or unknown.

    Only the innermost frame is compared, the one the exception was raised
    in. The caller chain above it is not evidence: the reporter ran a script
    and the reproduction ran pytest, so the outer frames legitimately differ
    on two runs of the very same bug. The innermost frame is the part both
    have in common when the bug is the same.

    A frame is compared on its function name and its file's basename. Line
    numbers drift with any commit, and the directory prefix differs between
    the reporter's checkout and the container (an installed package does not
    even keep the repository's own layout), so neither is stable enough to
    fail a run over.

    Returns (verdict, label, note). A file that differs is a mismatch even
    when neither side names a function, but a matching file alone is not
    enough for a match: naming the same function is the point.
    """
    if not expected or not observed:
        return "unknown", "", None
    left, right = expected[-1], observed[-1]
    detail = f"the issue's traceback raises in {describe_frame(left)}, "
    if _basename(left.path) != _basename(right.path):
        return "mismatch", "", detail + f"the run raised in {describe_frame(right)}"
    if left.symbol and right.symbol and left.symbol != right.symbol:
        return "mismatch", "", detail + f"the run raised in {describe_frame(right)}"
    if not left.symbol or not right.symbol:
        return "unknown", "", None
    return "match", f"{right.symbol} in {right.path}", None


def expected_signature(signals: Signals, text: str = "") -> FailureSignature:
    """The failure the issue describes.

    The exception and the frames both come from the last recognized stack
    trace that carries an error line, so they describe one failure rather
    than two halves of different ones. Failing tests come from two places:
    trace frames whose symbol is a test function in a test file, and pytest
    node ids written into the issue text by hand.
    """
    signature = FailureSignature()

    for trace in signals.traces:
        if not trace.error:
            continue
        exc_type, message = _split_exception(trace.error)
        if exc_type:
            signature.exception_type = exc_type
            signature.exception_message = message
            signature.frames = outermost_first(trace)
            signature.sources.append(f"{trace.language} stack trace in the issue")
            if trace.frames and "stack frames in the issue" not in signature.sources:
                signature.sources.append("stack frames in the issue")

    tests: list[str] = []
    for trace in signals.traces:
        for frame in trace.frames:
            symbol = frame.symbol or ""
            in_test_file = "test" in frame.path.rsplit("/", 1)[-1]
            if _TEST_NAME.match(symbol) and in_test_file and symbol not in tests:
                tests.append(symbol)
    if tests:
        signature.sources.append("test function name(s) in the stack trace")

    if signature.exception_type is None:
        # Gradle's default test output carries no stack trace, so the loop
        # above finds nothing and an issue quoting a Gradle log would name no
        # exception at all. `observed_signature` already falls back to this
        # same reader for the run's output; without the same fallback here the
        # two sides parse the same format differently, and a Gradle verdict
        # rests on matching test names alone. That is the weak case: a test
        # failing for an unrelated reason still has the reported name.
        gradle = gradle_exception(text)
        if gradle:
            signature.exception_type = gradle[0]
            signature.sources.append("Gradle failure line in the issue")

    for match in _NODE_ID.finditer(text):
        name = _test_name(match["nodeid"])
        if name not in tests:
            tests.append(name)
            if "pytest node id in the issue text" not in signature.sources:
                signature.sources.append("pytest node id in the issue text")

    runner_names, runner_source = runner_tests(text)
    known = {_test_name(name) for name in tests}
    for name in runner_names:
        # A traceback frame and the runner's own failure line name the same
        # test twice. Keep it once, under the name that was found first.
        if _test_name(name) not in known:
            known.add(_test_name(name))
            tests.append(name)
            if runner_source and f"{runner_source} in the issue text" not in signature.sources:
                signature.sources.append(f"{runner_source} in the issue text")

    signature.tests = tests
    return signature


def observed_signature(output: str, expect_type: str | None = None) -> FailureSignature:
    """The failure this run's output shows.

    Exception text is taken from the most specific source available, in
    order: pytest's "E   " echo and plain tracebacks (both untruncated),
    then pytest's short-summary line, which pytest may have truncated.
    Frames come from :func:`observed_frames`. Failing tests come from
    pytest's short summary, and when a run printed none, from the other
    runners' formats through :func:`runner_tests`.

    ``expect_type`` is a targeted fallback. The general matcher only
    recognizes exception names with a conventional suffix, so a project's
    ``MyBadThing`` would be invisible; when the issue names a type, that
    exact token is also searched for. Nothing is invented: the token still
    has to appear in the output as an exception line.
    """
    signature = FailureSignature()
    lines = output.splitlines()

    full: tuple[str, str | None] | None = None
    for line in lines:
        body = line.strip()
        e_line = _PYTEST_E_LINE.match(line)
        if e_line:
            body = e_line["body"].strip()
        exc_type, message = _split_exception(body)
        if exc_type is None and expect_type and body.startswith(expect_type):
            rest = body[len(expect_type) :]
            if rest.startswith(":") or not rest.strip():
                exc_type = expect_type
                message = rest.lstrip(":").strip() or None
        if exc_type:
            full = (exc_type, message)
            if e_line:
                if "pytest failure detail" not in signature.sources:
                    signature.sources.append("pytest failure detail")
            elif "exception line in the output" not in signature.sources:
                signature.sources.append("exception line in the output")

    if full is None:
        # A Rust panic announces itself as "thread 'main' panicked at
        # src/main.rs:5:14:" and puts the message on the NEXT line, so no
        # single line of the output is a recognizable failure on its own.
        # A JVM exception has the opposite problem: the type and message ARE
        # on one line, but that line is prefixed by `Exception in thread
        # "main" `, by `Caused by: `, or by JUnit's `=> `, none of which the
        # line scanner's anchored pattern will match.
        # The extractor has already handled both; ask it rather than
        # teaching the line scanner to carry state.
        for trace in native_traces(output):
            exc_type, message = _split_exception(trace.error or "")
            if exc_type:
                full = (exc_type, message)
                source = f"{_trace_source(trace.language)} line in the output"
                if source not in signature.sources:
                    signature.sources.append(source)

    if full is None:
        # Gradle is the one supported runner that prints no stack trace by
        # default, so neither the line scanner nor the extractors above find
        # anything in it. Its own failure line carries the type.
        gradle = gradle_exception(output)
        if gradle:
            full = gradle
            signature.sources.append("Gradle failure line in the output")

    tests: list[str] = []
    truncated_detail: tuple[str, str | None] | None = None
    for line in lines:
        summary = _PYTEST_SUMMARY.match(line.strip())
        if not summary:
            continue
        nodeid = summary["nodeid"]
        if nodeid not in tests:
            tests.append(nodeid)
        detail = (summary["detail"] or "").strip()
        if detail and truncated_detail is None:
            exc_type, message = _split_exception(detail.removesuffix(_ELLIPSIS))
            if exc_type:
                truncated_detail = (exc_type, message)
    if tests:
        signature.sources.append("pytest short summary")
    else:
        tests, runner_source = runner_tests(output)
        if runner_source:
            signature.sources.append(runner_source)

    if full is not None:
        signature.exception_type, signature.exception_message = full
    elif truncated_detail is not None:
        signature.exception_type, signature.exception_message = truncated_detail
        signature.message_truncated = True
        signature.sources.append("pytest short summary (message may be truncated)")

    frames, frame_source = observed_frames(output)
    signature.frames = frames
    if frame_source:
        signature.sources.append(frame_source)

    signature.tests = tests
    return signature


def _normalize(message: str) -> str:
    return _WHITESPACE.sub(" ", message).strip()


def compare_messages(expected: str | None, observed: str | None, truncated: bool) -> str:
    """Compare two exception messages: exact, close, different, or unknown.

    "close" covers the two ways a real message legitimately differs from the
    one in the issue: pytest truncated it, or it carries a path, an address,
    or a value that changed between machines. Word overlap is a heuristic and
    is reported as "close", never as "exact".
    """
    if expected is None or observed is None:
        return "unknown"
    left, right = _normalize(expected), _normalize(observed)
    if left == right:
        return "exact"
    if truncated and left.startswith(right):
        return "close"
    if left in right or right in left:
        return "close"
    left_words = set(re.findall(r"\w+", left.lower()))
    right_words = set(re.findall(r"\w+", right.lower()))
    if not left_words or not right_words:
        return "different"
    overlap = len(left_words & right_words) / len(left_words | right_words)
    return "close" if overlap >= 0.6 else "different"


def _same_exception_type(expected: str, observed: str) -> bool:
    """Whether two exception type names name the same class.

    Exact equality is the rule whenever both sides are qualified, because
    two packages may each define a `ValueError` and those are genuinely
    different classes. When only ONE side carries a package, the other did
    not record one rather than recording a different one, so the comparison
    falls back to the simple name.

    The JVM is what forces this. A reporter writes `NullPointerException`
    and the runtime prints `java.lang.NullPointerException`; requiring
    equality would call the same class a MISMATCH, which is worse than the
    `unknown` this feature replaces, because a mismatch is positive evidence
    against a reproduction. The rule is written for every language, not
    just the JVM: `requests.exceptions.HTTPError` against `HTTPError` has
    always had the same problem in Python.
    """
    if expected == observed:
        return True
    left, right = "." in expected, "." in observed
    if left == right:
        return False  # both qualified and different, or both bare and different
    return expected.rsplit(".", 1)[-1] == observed.rsplit(".", 1)[-1]


def compare_signatures(expected: FailureSignature, observed: FailureSignature) -> SignatureMatch:
    """Compare expected against observed, component by component."""
    match = SignatureMatch()

    if expected.exception_type is None:
        match.exception = "unknown"
        match.notes.append("the issue names no exception type")
    elif observed.exception_type is None:
        match.exception = "unknown"
        match.notes.append("no exception line was recognized in the run output")
    elif _same_exception_type(expected.exception_type, observed.exception_type):
        match.exception = "match"
    else:
        match.exception = "mismatch"
        match.notes.append(
            f"the issue reports {expected.exception_type}, the run raised {observed.exception_type}"
        )

    if match.exception == "match":
        match.message = compare_messages(
            expected.exception_message,
            observed.exception_message,
            observed.message_truncated,
        )
        if match.message == "different":
            match.notes.append("same exception type, but the message does not line up")
        elif match.message == "close" and observed.message_truncated:
            match.notes.append("pytest truncated the message; compared as a prefix")
    else:
        match.message = "unknown"

    if match.exception == "mismatch":
        # The run already failed with a different exception. Two failures can
        # share a frame and still be different bugs, so a frame that agrees
        # here is not evidence for anything and must not soften the verdict.
        match.frames = "unknown"
    else:
        match.frames, match.matched_frame, frame_note = compare_frames(
            expected.frames, observed.frames
        )
        if frame_note:
            match.notes.append(frame_note)

    if not expected.tests:
        match.tests = "unknown"
    elif not observed.tests:
        match.tests = "unknown"
        match.notes.append("the run reported no per-test failures to compare against")
    else:
        observed_names = {_test_name(nodeid) for nodeid in observed.tests}
        # Both sides are reduced to the bare test name, so a pytest node id,
        # a unittest id, and a cargo path all compare as the function they
        # name rather than as the string their runner happened to print.
        matched = [name for name in expected.tests if _test_name(name) in observed_names]
        match.matched_tests = matched
        if matched:
            match.tests = "match"
        else:
            match.tests = "mismatch"
            match.notes.append(
                f"the issue points at {', '.join(expected.tests[:3])}, "
                f"but the failing test(s) were {', '.join(sorted(observed.tests)[:3])}"
            )

    return match


def verdict_from_match(match: SignatureMatch) -> str:
    """Reduce the component comparison to one word.

    A single mismatch alongside a match is "partial", never "reproduced":
    the run failed in a way that is only partly the reported failure, and
    saying so is the point of the command.
    """
    components = match.components
    matches = sum(1 for c in components if c == "match" or c == "exact" or c == "close")
    mismatches = sum(1 for c in components if c in ("mismatch", "different"))
    if matches and mismatches:
        return "partial"
    if matches:
        return "reproduced"
    if mismatches:
        return "different-failure"
    return "unknown"
