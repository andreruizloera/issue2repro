"""Tests for the pure failure-signature comparison.

Everything here is text in, verdict out. No subprocess, no Docker, no clone.
"""

from issue2repro.extract import extract_signals
from issue2repro.models import FailureSignature, Signals, StackFrame, StackTrace
from issue2repro.signature import (
    compare_messages,
    compare_signatures,
    expected_signature,
    observed_signature,
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
        assert match.components == ("unknown", "unknown", "unknown")
        assert verdict_from_match(match) == "unknown"

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
