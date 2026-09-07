"""Tests for the pure failure-signature comparison.

Everything here is text in, verdict out. No subprocess, no Docker, no clone.
"""

from issue2repro.extract import extract_signals
from issue2repro.models import FailureSignature, Signals, StackFrame, StackTrace
from issue2repro.signature import (
    compare_frames,
    compare_messages,
    compare_signatures,
    expected_signature,
    observed_frames,
    observed_signature,
    pytest_frames,
    verdict_from_match,
)

PYTEST_OUTPUT = """\
=================================== FAILURES ===================================
____________________________ test_negative_operand _____________________________

    def test_negative_operand():
>       assert evaluate("2 + -3") == -1

tests/test_evaluate.py:13:
E           ValueError: invalid literal for int() with base 10: '-'

src/tinycalc/evaluate.py:23: ValueError
=========================== short test summary info ============================
FAILED tests/test_evaluate.py::test_negative_operand - ValueError: invalid li...
========================= 1 failed, 2 passed in 0.05s ==========================
"""

# pytest's default traceback, verbatim from a real run of the tinycalc
# fixture. It names no frame the way Python does; each frame ends in a
# "path:line:" line, and the function has to be read from the echoed source.
PYTEST_LONG_OUTPUT = """\
=================================== FAILURES ===================================
____________________________ test_negative_operand _____________________________

    def test_negative_operand():
>       assert evaluate("2 + -3") == -1

tests/test_evaluate.py:13:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

expr = '2 + -3'

    def evaluate(expr: str) -> int:
        tokens = tokenize(expr)
        for i in range(1, len(tokens), 2):
>           value = int(tokens[i + 1])
E           ValueError: invalid literal for int() with base 10: '-'

src/tinycalc/evaluate.py:23: ValueError
=========================== short test summary info ============================
FAILED tests/test_evaluate.py::test_negative_operand - ValueError: invalid li...
========================= 1 failed, 2 passed in 0.09s ==========================
"""

# Verbatim from Python 3.13, anchor line included.
PLAIN_TRACEBACK = """\
Traceback (most recent call last):
  File "/tmp/repro/source/tools/repro.py", line 5, in <module>
    widen("-")
    ~~~~~^^^^^
  File "/tmp/repro/source/tools/repro.py", line 2, in widen
    raise ValueError("invalid literal for int() with base 10: '-'")
ValueError: invalid literal for int() with base 10: '-'
"""


def frame(path: str, line: int, symbol: str | None) -> StackFrame:
    return StackFrame(path=path, line=line, symbol=symbol)


class TestExpectedSignature:
    def test_reads_exception_and_test_from_a_traceback(self, python_issue):
        signature = expected_signature(extract_signals(python_issue.full_text))
        assert signature.exception_type == "UnicodeDecodeError"
        assert signature.tests == ["test_unicode"]
        assert not signature.is_empty

    def test_splits_type_from_message(self):
        signals = Signals(
            traces=[
                StackTrace(
                    language="python",
                    frames=[StackFrame(path="app.py", line=1)],
                    error="ValueError: invalid literal for int() with base 10: '-'",
                )
            ]
        )
        signature = expected_signature(signals)
        assert signature.exception_type == "ValueError"
        assert signature.exception_message == "invalid literal for int() with base 10: '-'"

    def test_a_vague_issue_yields_nothing_checkable(self, vague_issue):
        signature = expected_signature(extract_signals(vague_issue.full_text))
        assert signature.is_empty
        assert signature.describe() == "nothing checkable"

    def test_test_symbol_outside_a_test_file_is_not_a_test(self):
        signals = Signals(
            traces=[
                StackTrace(
                    language="python",
                    frames=[StackFrame(path="src/app/runner.py", line=4, symbol="test_harness")],
                    error="RuntimeError: boom",
                )
            ]
        )
        assert expected_signature(signals).tests == []

    def test_node_id_written_by_hand_in_the_issue_text(self):
        text = "It only fails under tests/test_cart.py::test_discount[10] on my machine."
        signature = expected_signature(Signals(), text)
        assert signature.tests == ["test_discount"]
        assert "pytest node id in the issue text" in signature.sources

    def test_frames_come_from_the_trace_that_carried_the_exception(self):
        signals = Signals(
            traces=[
                StackTrace(
                    language="python",
                    frames=[
                        frame("tests/test_cart.py", 9, "test_discount"),
                        frame("src/shop/pricing.py", 44, "apply_discount"),
                    ],
                    error="KeyError: 'currency'",
                )
            ]
        )
        signature = expected_signature(signals)
        assert [f.symbol for f in signature.frames] == ["test_discount", "apply_discount"]
        assert signature.frames[-1].path == "src/shop/pricing.py"

    def test_node_frames_are_reversed_so_the_innermost_is_last(self):
        signals = Signals(
            traces=[
                StackTrace(
                    language="node",
                    frames=[
                        frame("/app/src/cart.js", 12, "applyDiscount"),
                        frame("/app/src/index.js", 3, "main"),
                    ],
                    error="TypeError: cannot read properties of undefined",
                )
            ]
        )
        signature = expected_signature(signals)
        assert signature.frames[-1].symbol == "applyDiscount"

    def test_an_issue_with_no_traceback_carries_no_frames(self, vague_issue):
        assert expected_signature(extract_signals(vague_issue.full_text)).frames == []

    def test_node_id_and_trace_do_not_duplicate_a_test(self):
        signals = Signals(
            traces=[
                StackTrace(
                    language="python",
                    frames=[StackFrame(path="tests/test_cart.py", line=9, symbol="test_discount")],
                    error="KeyError: 'discount'",
                )
            ]
        )
        signature = expected_signature(signals, "see tests/test_cart.py::test_discount")
        assert signature.tests == ["test_discount"]


class TestObservedSignature:
    def test_prefers_the_untruncated_pytest_failure_detail(self):
        observed = observed_signature(PYTEST_OUTPUT)
        assert observed.exception_type == "ValueError"
        assert observed.exception_message == "invalid literal for int() with base 10: '-'"
        assert observed.message_truncated is False
        assert observed.tests == ["tests/test_evaluate.py::test_negative_operand"]

    def test_short_summary_alone_is_marked_truncated(self):
        output = (
            "=========================== short test summary info ====================\n"
            "FAILED tests/test_evaluate.py::test_negative_operand - "
            "ValueError: invalid li...\n"
        )
        observed = observed_signature(output)
        assert observed.exception_type == "ValueError"
        assert observed.exception_message == "invalid li"
        assert observed.message_truncated is True

    def test_reads_a_plain_traceback(self):
        output = (
            "Traceback (most recent call last):\n"
            '  File "app.py", line 2, in <module>\n'
            "    main()\n"
            "KeyError: 'currency'\n"
        )
        observed = observed_signature(output)
        assert observed.exception_type == "KeyError"
        assert observed.exception_message == "'currency'"

    def test_prose_with_a_colon_is_not_an_exception(self):
        output = "Note: the build finished\nSummary: everything is fine\n"
        assert observed_signature(output).exception_type is None

    def test_expected_type_finds_a_custom_exception_name(self):
        output = "MyBadThing: the widget fell over\n"
        assert observed_signature(output).exception_type is None
        targeted = observed_signature(output, expect_type="MyBadThing")
        assert targeted.exception_type == "MyBadThing"
        assert targeted.exception_message == "the widget fell over"

    def test_expected_type_is_not_invented_when_absent(self):
        output = "everything passed\n"
        assert observed_signature(output, expect_type="MyBadThing").exception_type is None

    def test_errors_are_collected_alongside_failures(self):
        output = (
            "ERROR tests/test_setup.py::test_boot - ImportError: no module named x\n"
            "FAILED tests/test_a.py::test_b - AssertionError: nope\n"
        )
        observed = observed_signature(output)
        assert observed.tests == [
            "tests/test_setup.py::test_boot",
            "tests/test_a.py::test_b",
        ]

    def test_clean_output_has_no_signature(self):
        assert observed_signature("3 passed in 0.10s\n").is_empty


class TestObservedFrames:
    def test_pytest_long_traceback_names_each_frames_function(self):
        frames = pytest_frames(PYTEST_LONG_OUTPUT)
        assert [(f.path, f.symbol) for f in frames] == [
            ("tests/test_evaluate.py", "test_negative_operand"),
            ("src/tinycalc/evaluate.py", "evaluate"),
        ]

    def test_short_traceback_frames_name_their_function_directly(self):
        output = (
            "tests/test_cart.py:9: in test_discount\n"
            "    apply_discount(cart)\n"
            "src/shop/pricing.py:44: in apply_discount\n"
            "    return line['currency']\n"
            "E   KeyError: 'currency'\n"
        )
        frames = pytest_frames(output)
        assert [f.symbol for f in frames] == ["test_discount", "apply_discount"]

    def test_a_plain_traceback_is_read_when_pytest_printed_none(self):
        frames, source = observed_frames(PLAIN_TRACEBACK)
        assert [f.symbol for f in frames] == ["<module>", "widen"]
        assert source == "traceback frames in the output"

    def test_node_stack_frames_are_reversed_so_the_innermost_is_last(self):
        output = (
            "TypeError: cannot read properties of undefined\n"
            "    at applyDiscount (/app/src/cart.js:12:9)\n"
            "    at main (/app/src/index.js:3:1)\n"
        )
        frames, source = observed_frames(output)
        assert [f.symbol for f in frames] == ["main", "applyDiscount"]
        assert source == "node stack frames in the output"

    def test_output_with_no_frames_yields_none(self):
        frames, source = observed_frames("3 passed in 0.10s\n")
        assert frames == []
        assert source is None

    def test_the_signature_records_the_frames_and_their_source(self):
        observed = observed_signature(PYTEST_LONG_OUTPUT)
        assert observed.frames[-1].symbol == "evaluate"
        assert "pytest traceback frames" in observed.sources


class TestCompareFrames:
    def test_same_function_and_file_is_a_match(self):
        verdict, label, note = compare_frames(
            [frame("src/shop/pricing.py", 44, "apply_discount")],
            [frame("src/shop/pricing.py", 51, "apply_discount")],
        )
        assert verdict == "match"
        assert label == "apply_discount in src/shop/pricing.py"
        assert note is None

    def test_the_directory_prefix_and_line_number_are_ignored(self):
        verdict, _, _ = compare_frames(
            [frame("tools/repro.py", 2, "widen")],
            [frame("/tmp/xyz/source/tools/repro.py", 900, "widen")],
        )
        assert verdict == "match"

    def test_the_same_exception_in_another_function_is_a_mismatch(self):
        verdict, _, note = compare_frames(
            [frame("src/tinycalc/evaluate.py", 12, "tokenize")],
            [frame("src/tinycalc/evaluate.py", 23, "evaluate")],
        )
        assert verdict == "mismatch"
        assert "tokenize" in note and "evaluate" in note

    def test_the_same_function_name_in_another_file_is_a_mismatch(self):
        verdict, _, note = compare_frames(
            [frame("src/shop/pricing.py", 44, "load")],
            [frame("src/shop/config.py", 44, "load")],
        )
        assert verdict == "mismatch"
        assert "config.py" in note

    def test_only_the_innermost_frame_is_compared(self):
        """The caller chain differs between a reporter's script and pytest."""
        verdict, _, _ = compare_frames(
            [frame("run_me.py", 1, "<module>"), frame("src/shop/pricing.py", 44, "apply")],
            [frame("tests/test_cart.py", 9, "test_discount"), frame("pricing.py", 44, "apply")],
        )
        assert verdict == "match"

    def test_a_missing_side_is_unknown(self):
        assert compare_frames([], [frame("a.py", 1, "f")])[0] == "unknown"
        assert compare_frames([frame("a.py", 1, "f")], [])[0] == "unknown"

    def test_an_unnamed_function_on_one_side_is_unknown_not_a_match(self):
        verdict, _, _ = compare_frames(
            [frame("src/shop/pricing.py", 44, "apply_discount")],
            [frame("src/shop/pricing.py", 44, None)],
        )
        assert verdict == "unknown"


class TestCompareMessages:
    def test_exact_ignores_whitespace_runs(self):
        assert compare_messages("a  b", "a b", truncated=False) == "exact"

    def test_truncated_prefix_is_close(self):
        assert (
            compare_messages(
                "invalid literal for int() with base 10: '-'", "invalid li", truncated=True
            )
            == "close"
        )

    def test_containment_is_close(self):
        assert compare_messages("cannot open 'x'", "open 'x'", truncated=False) == "close"

    def test_word_overlap_is_close(self):
        assert (
            compare_messages(
                "cannot connect to database on port 5432",
                "cannot connect to database on port 5433",
                truncated=False,
            )
            == "close"
        )

    def test_unrelated_messages_are_different(self):
        assert compare_messages("'currency'", "'discount'", truncated=False) == "different"

    def test_a_missing_side_is_unknown(self):
        assert compare_messages(None, "boom", truncated=False) == "unknown"


class TestCompareSignatures:
    def test_full_match(self):
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="invalid literal for int() with base 10: '-'",
            tests=["test_negative_operand"],
        )
        match = compare_signatures(expected, observed_signature(PYTEST_OUTPUT))
        assert match.exception == "match"
        assert match.message == "exact"
        assert match.tests == "match"
        assert match.matched_tests == ["test_negative_operand"]
        assert verdict_from_match(match) == "reproduced"

    def test_parametrized_case_matches_the_bare_test_name(self):
        expected = FailureSignature(exception_type="KeyError", tests=["test_discount"])
        observed = FailureSignature(
            exception_type="KeyError", tests=["tests/test_cart.py::test_discount[10]"]
        )
        assert compare_signatures(expected, observed).tests == "match"

    def test_different_exception_and_test_is_a_different_failure(self):
        expected = FailureSignature(
            exception_type="ZeroDivisionError", tests=["test_divide_by_zero"]
        )
        match = compare_signatures(expected, observed_signature(PYTEST_OUTPUT))
        assert match.exception == "mismatch"
        assert match.tests == "mismatch"
        assert verdict_from_match(match) == "different-failure"

    def test_same_test_different_exception_is_partial(self):
        expected = FailureSignature(exception_type="TypeError", tests=["test_negative_operand"])
        match = compare_signatures(expected, observed_signature(PYTEST_OUTPUT))
        assert match.exception == "mismatch"
        assert match.tests == "match"
        assert verdict_from_match(match) == "partial"

    def test_same_type_unrelated_message_is_partial(self):
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="expected a positive integer",
            tests=["test_negative_operand"],
        )
        match = compare_signatures(expected, observed_signature(PYTEST_OUTPUT))
        assert match.exception == "match"
        assert match.message == "different"
        assert verdict_from_match(match) == "partial"

    def test_exception_only_on_both_sides_is_reproduced(self):
        expected = FailureSignature(exception_type="KeyError", exception_message="'currency'")
        observed = FailureSignature(exception_type="KeyError", exception_message="'currency'")
        match = compare_signatures(expected, observed)
        assert match.tests == "unknown"
        assert verdict_from_match(match) == "reproduced"

    def test_nothing_comparable_is_unknown_not_a_match(self):
        match = compare_signatures(FailureSignature(), FailureSignature())
        assert match.components == ("unknown", "unknown", "unknown", "unknown")
        assert verdict_from_match(match) == "unknown"

    def test_the_same_exception_in_another_function_is_not_a_reproduction(self):
        """The headline case: type, message, and test all line up, and the
        failure still came out of a different function."""
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="invalid literal for int() with base 10: '-'",
            frames=[
                StackFrame(path="tests/test_evaluate.py", line=13, symbol="test_negative_operand"),
                StackFrame(path="src/tinycalc/evaluate.py", line=12, symbol="tokenize"),
            ],
            tests=["test_negative_operand"],
        )
        match = compare_signatures(expected, observed_signature(PYTEST_LONG_OUTPUT))
        assert match.exception == "match"
        assert match.message == "exact"
        assert match.tests == "match"
        assert match.frames == "mismatch"
        assert verdict_from_match(match) == "partial"
        assert any("tokenize" in note and "evaluate" in note for note in match.notes)

    def test_the_same_exception_in_the_same_function_stays_reproduced(self):
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="invalid literal for int() with base 10: '-'",
            frames=[StackFrame(path="src/tinycalc/evaluate.py", line=23, symbol="evaluate")],
            tests=["test_negative_operand"],
        )
        match = compare_signatures(expected, observed_signature(PYTEST_LONG_OUTPUT))
        assert match.frames == "match"
        assert match.matched_frame == "evaluate in src/tinycalc/evaluate.py"
        assert verdict_from_match(match) == "reproduced"

    def test_an_issue_with_no_traceback_leaves_the_verdict_untouched(self):
        """No frames on the expected side is unknown, and unknown is never
        evidence: the verdict must be exactly what it was before frames
        were compared at all."""
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="invalid literal for int() with base 10: '-'",
            tests=["test_negative_operand"],
        )
        match = compare_signatures(expected, observed_signature(PYTEST_LONG_OUTPUT))
        assert expected.frames == []
        assert match.frames == "unknown"
        assert verdict_from_match(match) == "reproduced"

    def test_a_run_that_prints_no_frames_is_unknown_not_a_mismatch(self):
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="boom",
            frames=[StackFrame(path="src/app.py", line=3, symbol="handle")],
        )
        observed = FailureSignature(exception_type="ValueError", exception_message="boom")
        match = compare_signatures(expected, observed)
        assert match.frames == "unknown"
        assert verdict_from_match(match) == "reproduced"

    def test_frames_are_not_compared_under_a_different_exception(self):
        """Two failures raised in the same function are still two failures.
        A frame that agrees must not soften a clear different-failure."""
        expected = FailureSignature(
            exception_type="ZeroDivisionError",
            exception_message="integer division or modulo by zero",
            frames=[StackFrame(path="src/tinycalc/evaluate.py", line=27, symbol="evaluate")],
            tests=["test_divide_by_zero"],
        )
        match = compare_signatures(expected, observed_signature(PYTEST_LONG_OUTPUT))
        assert match.exception == "mismatch"
        assert match.frames == "unknown"
        assert verdict_from_match(match) == "different-failure"

    def test_an_unrecognized_run_output_does_not_count_against_the_issue(self):
        expected = FailureSignature(exception_type="ValueError", tests=["test_x"])
        match = compare_signatures(expected, FailureSignature())
        assert match.exception == "unknown"
        assert match.tests == "unknown"
        assert verdict_from_match(match) == "unknown"

    def test_truncation_note_is_recorded(self):
        expected = FailureSignature(
            exception_type="ValueError",
            exception_message="invalid literal for int() with base 10: '-'",
        )
        observed = FailureSignature(
            exception_type="ValueError",
            exception_message="invalid li",
            message_truncated=True,
        )
        match = compare_signatures(expected, observed)
        assert match.message == "close"
        assert any("truncated" in note for note in match.notes)
