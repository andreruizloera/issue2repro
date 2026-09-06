"""Failure signatures: what the issue says broke, what a run actually did.

Pure functions over strings and Signals, so the whole comparison is
testable offline without running anything. The module answers three
questions and nothing else:

- what failure does the issue describe (:func:`expected_signature`)
- what failure did this output show (:func:`observed_signature`)
- do they match, component by component (:func:`compare_signatures`)

Everything here is a heuristic over text, and it is labeled as one: a
"match" means the exception type and the failing test line up, not that
the two runs executed the same code path.
"""

from __future__ import annotations

import re

from issue2repro.models import FailureSignature, Signals, SignatureMatch

# An exception line, as printed by Python, Node, or pytest's "E   " echo.
# The type name must end in a recognizable suffix, which keeps ordinary
# prose with a colon in it from being read as an exception.
_EXC_SUFFIXES = r"(?:Error|Exception|Warning|Interrupt|Exit|StopIteration|Failure|Failed)"
_EXC_LINE = re.compile(rf"^(?P<type>[A-Za-z_][\w.]*{_EXC_SUFFIXES})(?::\s*(?P<message>.*))?$")

# pytest's short summary: "FAILED tests/test_x.py::test_y - ValueError: boom"
_PYTEST_SUMMARY = re.compile(
    r"^(?:FAILED|ERROR)\s+(?P<nodeid>[^\s]+?::[^\s]+|[^\s]+\.py)(?:\s+-\s+(?P<detail>.*))?$"
)
# pytest echoes the failing assertion/exception under "E   " in the failure body.
_PYTEST_E_LINE = re.compile(r"^E\s{2,}(?P<body>.+)$")

# A pytest node id written by hand in an issue: "tests/test_x.py::test_y".
_NODE_ID = re.compile(r"(?P<nodeid>[\w./-]+\.py::[\w\[\]:.\-]+)")

_TEST_NAME = re.compile(r"^test[\w]*$")

# pytest truncates the short-summary detail with a trailing ellipsis.
_ELLIPSIS = "..."

_WHITESPACE = re.compile(r"\s+")


def _split_exception(text: str) -> tuple[str | None, str | None]:
    """Split 'ValueError: boom' into ('ValueError', 'boom')."""
    match = _EXC_LINE.match(text.strip())
    if not match:
        return None, None
    message = match["message"]
    return match["type"], message.strip() if message else None


def _test_name(nodeid: str) -> str:
    """The bare test function name from a node id, parametrization stripped."""
    tail = nodeid.rsplit("::", 1)[-1]
    return tail.split("[", 1)[0]


def expected_signature(signals: Signals, text: str = "") -> FailureSignature:
    """The failure the issue describes.

    The exception comes from the last recognized stack trace that carries an
    error line. Failing tests come from two places: trace frames whose symbol
    is a test function in a test file, and pytest node ids written into the
    issue text by hand.
    """
    signature = FailureSignature()

    for trace in signals.traces:
        if not trace.error:
            continue
        exc_type, message = _split_exception(trace.error)
        if exc_type:
            signature.exception_type = exc_type
            signature.exception_message = message
            signature.sources.append(f"{trace.language} stack trace in the issue")

    tests: list[str] = []
    for trace in signals.traces:
        for frame in trace.frames:
            symbol = frame.symbol or ""
            in_test_file = "test" in frame.path.rsplit("/", 1)[-1]
            if _TEST_NAME.match(symbol) and in_test_file and symbol not in tests:
                tests.append(symbol)
    if tests:
        signature.sources.append("test function name(s) in the stack trace")

    for match in _NODE_ID.finditer(text):
        name = _test_name(match["nodeid"])
        if name not in tests:
            tests.append(name)
            if "pytest node id in the issue text" not in signature.sources:
                signature.sources.append("pytest node id in the issue text")

    signature.tests = tests
    return signature


def observed_signature(output: str, expect_type: str | None = None) -> FailureSignature:
    """The failure this run's output shows.

    Exception text is taken from the most specific source available, in
    order: pytest's "E   " echo and plain tracebacks (both untruncated),
    then pytest's short-summary line, which pytest may have truncated.

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

    if full is not None:
        signature.exception_type, signature.exception_message = full
    elif truncated_detail is not None:
        signature.exception_type, signature.exception_message = truncated_detail
        signature.message_truncated = True
        signature.sources.append("pytest short summary (message may be truncated)")

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


def compare_signatures(expected: FailureSignature, observed: FailureSignature) -> SignatureMatch:
    """Compare expected against observed, component by component."""
    match = SignatureMatch()

    if expected.exception_type is None:
        match.exception = "unknown"
        match.notes.append("the issue names no exception type")
    elif observed.exception_type is None:
        match.exception = "unknown"
        match.notes.append("no exception line was recognized in the run output")
    elif expected.exception_type == observed.exception_type:
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

    if not expected.tests:
        match.tests = "unknown"
    elif not observed.tests:
        match.tests = "unknown"
        match.notes.append("the run reported no per-test failures to compare against")
    else:
        observed_names = {_test_name(nodeid) for nodeid in observed.tests}
        matched = [name for name in expected.tests if name in observed_names]
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
