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
    gradle_exception,
    observed_frames,
    observed_signature,
    pytest_frames,
    runner_tests,
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

    def test_a_unittest_failure_pasted_into_an_issue_names_the_test(self):
        signature = expected_signature(Signals(), UNITTEST_OUTPUT)
        assert signature.tests == ["test_widen.WidenTest::test_widen"]
        assert "unittest failure lines in the issue text" in signature.sources

    def test_a_cargo_failure_pasted_into_an_issue_names_the_test(self):
        text = "It panics:\n\n---- tests::divides_by_zero stdout ----\nattempt to divide\n"
        assert expected_signature(Signals(), text).tests == ["tests::divides_by_zero"]

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

    def test_a_unittest_run_reports_its_failing_test(self):
        observed = observed_signature(UNITTEST_OUTPUT)
        assert observed.tests == ["test_widen.WidenTest::test_widen"]
        assert observed.exception_type == "ValueError"
        assert observed.frames[-1].symbol == "widen"
        assert "unittest failure lines" in observed.sources

    def test_pytest_wins_when_both_formats_are_present(self):
        """pytest can run unittest classes, and then both lines appear. The
        pytest node id is the more precise of the two."""
        observed = observed_signature(PYTEST_OUTPUT + UNITTEST_OUTPUT)
        assert observed.tests == ["tests/test_evaluate.py::test_negative_operand"]


UNITTEST_OUTPUT = """\
E
======================================================================
ERROR: test_widen (test_widen.WidenTest.test_widen)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/repro/source/test_widen.py", line 10, in test_widen
    widen("-")
    ~~~~~^^^^^
  File "/tmp/repro/source/test_widen.py", line 5, in widen
    raise ValueError("invalid literal for int() with base 10: '-'")
ValueError: invalid literal for int() with base 10: '-'

----------------------------------------------------------------------
Ran 1 test in 0.001s

FAILED (errors=1)
"""


class TestRunnerTests:
    """Per-test failures from the runners that are not pytest."""

    def test_unittest_on_3_11_and_later(self):
        names, source = runner_tests(UNITTEST_OUTPUT)
        assert names == ["test_widen.WidenTest::test_widen"]
        assert source == "unittest failure lines"

    def test_unittest_before_3_11_produces_the_same_id(self):
        older = UNITTEST_OUTPUT.replace(
            "test_widen (test_widen.WidenTest.test_widen)",
            "test_widen (test_widen.WidenTest)",
        )
        assert runner_tests(older)[0] == ["test_widen.WidenTest::test_widen"]

    def test_go_test_reports_the_test_and_its_subtest(self):
        output = (
            "--- FAIL: TestDivide (0.00s)\n"
            "    --- FAIL: TestDivide/by_zero (0.00s)\n"
            "        calc_test.go:21: expected 3, got 0\n"
            "FAIL\n"
            "FAIL\texample.com/calc\t0.005s\n"
        )
        names, source = runner_tests(output)
        assert names == ["TestDivide", "TestDivide/by_zero"]
        assert source == "go test failure lines"

    def test_cargo_test(self):
        output = (
            "failures:\n"
            "\n"
            "---- tests::divides_by_zero stdout ----\n"
            "thread 'tests::divides_by_zero' panicked at src/lib.rs:12:5:\n"
            "attempt to divide by zero\n"
            "\n"
            "test result: FAILED. 1 passed; 1 failed; 0 ignored\n"
        )
        names, source = runner_tests(output)
        assert names == ["tests::divides_by_zero"]
        assert source == "cargo test failure lines"

    def test_node_test_tap_output(self):
        output = (
            "TAP version 13\n"
            "# Subtest: applies a discount\n"
            "not ok 1 - applies a discount\n"
            "  ---\n"
            "  failureType: 'testCodeFailure'\n"
            "  ...\n"
            "ok 2 - adds an item\n"
        )
        names, source = runner_tests(output)
        assert names == ["applies a discount"]
        assert source == "node --test failure lines"

    def test_a_skipped_tap_test_is_not_a_failure(self):
        assert runner_tests("not ok 1 - applies a discount # SKIP\n")[0] == []

    def test_node_test_spec_output(self):
        names, source = runner_tests("  ✖ applies a discount (1.234ms)\n")
        assert names == ["applies a discount"]
        assert source == "node --test failure lines"

    def test_prose_is_not_a_test_failure(self):
        assert runner_tests("ERROR: could not install\nFAIL\nfailures:\n")[0] == []

    def test_output_from_no_runner_at_all(self):
        assert runner_tests("nothing to see here\n") == ([], None)


class TestMavenSurefireTests:
    """Failing test names from real `mvn test` output."""

    def test_surefire_names_every_failing_test(self, jvm_maven_surefire):
        names, source = runner_tests(jvm_maven_surefire)
        assert source == "Maven Surefire failure lines"
        assert names == [
            "ShippingTest::flatRateUnderThreshold",
            "ShippingTest::freeOverFiftyDollars",
            "PricingTest::unknownCouponIsIgnored",
        ]

    def test_the_class_level_line_is_not_read_as_a_test(self, jvm_maven_surefire):
        """The hazard: it ends in "<<< FAILURE!" exactly like a test line.

        Surefire announces each class with "Tests run: 3, Failures: 3, ...
        <<< FAILURE! -- in com.example.shop.ShippingTest". If that were
        read, every Surefire run would report a test named after a count.
        """
        assert "Tests run: 3, Failures: 3, Errors: 0, Skipped: 0" in jvm_maven_surefire
        names, _ = runner_tests(jvm_maven_surefire)
        assert all("Tests" not in name.split("::")[-1] for name in names)
        assert all("run" not in name.split("::")[0] for name in names)

    def test_an_assertion_failure_and_an_exception_both_count(self):
        """Surefire says FAILURE! for an assertion and ERROR! otherwise."""
        output = (
            "[ERROR] com.example.shop.ShippingTest.freeOverFiftyDollars"
            " -- Time elapsed: 0.007 s <<< FAILURE!\n"
            "[ERROR] com.example.shop.PricingTest.unknownCouponIsIgnored"
            " -- Time elapsed: 0.003 s <<< ERROR!\n"
        )
        names, source = runner_tests(output)
        assert source == "Maven Surefire failure lines"
        assert names == [
            "ShippingTest::freeOverFiftyDollars",
            "PricingTest::unknownCouponIsIgnored",
        ]

    def test_a_parametrized_case_reduces_to_one_test(self):
        """ "(int)[1]" and "(int)[2]" are one test, run twice."""
        output = (
            "[ERROR] com.example.shop.ShippingTest.flatRateUnderThreshold(int)[1]"
            " -- Time elapsed: 0.067 s <<< FAILURE!\n"
            "[ERROR] com.example.shop.ShippingTest.flatRateUnderThreshold(int)[2]"
            " -- Time elapsed: 0.004 s <<< FAILURE!\n"
        )
        assert runner_tests(output)[0] == ["ShippingTest::flatRateUnderThreshold"]

    def test_the_summary_block_alone_is_enough(self):
        """An issue reporter pastes the tail of a build, not the whole log."""
        output = (
            "[ERROR] Failures: \n"
            "[ERROR]   ShippingTest.freeOverFiftyDollars:13 expected: <0> but was: <599>\n"
            "[ERROR] Errors: \n"
            "[ERROR]   PricingTest.unknownCouponIsIgnored:22 » NullPointer Cannot invoke\n"
        )
        names, source = runner_tests(output)
        assert source == "Maven Surefire failure lines"
        assert names == [
            "ShippingTest::freeOverFiftyDollars",
            "PricingTest::unknownCouponIsIgnored",
        ]

    def test_a_full_log_does_not_double_count(self, jvm_maven_surefire):
        """The per-test lines and the summary describe the same failures."""
        names, _ = runner_tests(jvm_maven_surefire)
        assert len(names) == len(set(names))

    def test_surefire_stack_frames_are_not_read_as_tests(self):
        """ "at com.example.shop.Pricing.applyCoupon(Pricing.java:13)" is a frame."""
        output = "\tat com.example.shop.Pricing.applyCoupon(Pricing.java:13)\n"
        assert runner_tests(output)[0] == []


class TestGradleTests:
    """Failing test names and exception type from real `gradle test` output."""

    def test_gradle_names_every_failing_test(self, jvm_gradle_test):
        names, source = runner_tests(jvm_gradle_test)
        assert source == "Gradle failure lines"
        assert names == [
            "PricingTest::unknownCouponIsIgnored",
            "ShippingTest::flatRateUnderThreshold",
            "ShippingTest::freeOverFiftyDollars",
        ]

    def test_the_task_line_is_not_read_as_a_test(self, jvm_gradle_test):
        """ "> Task :test FAILED" ends in FAILED and names no method."""
        assert "> Task :test FAILED" in jvm_gradle_test
        names, _ = runner_tests(jvm_gradle_test)
        assert all("Task" not in name for name in names)
        assert runner_tests("> Task :test FAILED\n")[0] == []

    def test_a_parametrized_display_name_is_not_the_test(self):
        """The method is not the last segment when a case has a display name."""
        line = "ShippingTest > flatRateUnderThreshold(int) > [1] 100 FAILED\n"
        assert runner_tests(line)[0] == ["ShippingTest::flatRateUnderThreshold"]

    def test_a_nested_class_keeps_the_innermost_class(self):
        """@Nested adds a segment, so the method is deeper than depth two."""
        line = "OrderTest > WhenEmpty > totalIsZero() FAILED\n"
        assert runner_tests(line)[0] == ["WhenEmpty::totalIsZero"]

    def test_gradle_exception_type_is_read(self, jvm_gradle_test):
        assert gradle_exception(jvm_gradle_test) == ("java.lang.NullPointerException", None)

    def test_the_message_is_left_unknown_rather_than_invented(self, jvm_gradle_test):
        """Gradle prints no message, so none is reported."""
        signature = observed_signature(jvm_gradle_test)
        assert signature.exception_type == "java.lang.NullPointerException"
        assert signature.exception_message is None

    def test_a_location_without_a_failed_line_above_it_is_not_an_exception(self):
        """The anchor is position: prose can reach this shape otherwise."""
        assert gradle_exception("java.lang.IllegalStateException at Foo.java:3\n") is None
        assert gradle_exception("    java.lang.IllegalStateException at Foo.java:3\n") is None

    def test_gradle_reports_no_frames_and_says_so(self, jvm_gradle_test):
        """Gradle's default output carries no stack, so frames stay empty.

        The location on the failure line names no symbol, and is
        deliberately not turned into a frame, for the same reason a Rust
        panic without RUST_BACKTRACE compares as unknown.
        """
        assert observed_signature(jvm_gradle_test).frames == []

    def test_exception_format_full_buys_a_message_but_not_frames(self, jvm_gradle_full_exception):
        """Pins what `testLogging { exceptionFormat "full" }` actually does.

        The README states this, so it is measured here rather than
        asserted there. The setting does print a real stack trace, and the
        type and message become readable from it, but the frames still
        attach to no trace: Gradle indents the exception header under the
        FAILED line and the JVM extractor anchors a header at column zero.
        Reading them is a ROADMAP item, and this test is what will fail
        when it is done.
        """
        assert "        at com.example.shop.Pricing.applyCoupon" in jvm_gradle_full_exception
        signature = observed_signature(jvm_gradle_full_exception)
        assert signature.exception_message is not None
        assert signature.frames == []
        assert signature.tests == [
            "PricingTest::unknownCouponIsIgnored",
            "ShippingTest::flatRateUnderThreshold",
            "ShippingTest::freeOverFiftyDollars",
        ]


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

    def test_a_unittest_id_compares_as_the_test_it_names(self):
        expected = expected_signature(extract_signals(UNITTEST_OUTPUT), UNITTEST_OUTPUT)
        match = compare_signatures(expected, observed_signature(UNITTEST_OUTPUT))
        assert match.exception == "match"
        assert match.frames == "match"
        assert match.tests == "match"
        # the traceback frame named it first, so the runner id is not repeated
        assert expected.tests == ["test_widen"]
        assert match.matched_tests == ["test_widen"]
        assert verdict_from_match(match) == "reproduced"

    def test_a_different_test_under_the_same_runner_is_a_mismatch(self):
        expected = FailureSignature(
            exception_type="ValueError", tests=["test_widen.WidenTest::test_widen"]
        )
        observed = observed_signature(
            UNITTEST_OUTPUT.replace("test_widen (test_widen", "test_narrow (test_widen")
        )
        match = compare_signatures(expected, observed)
        assert match.tests == "mismatch"

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


class TestNativePanicSignatures:
    """Go and Rust, end to end: issue text in, verdict out.

    Every panic in this class was captured from a real toolchain run
    (go1.27.1, rustc 1.98.0) rather than written from memory. Before this
    worked, a Go or Rust report produced an EMPTY expected signature and the
    verdict was `unknown`: the tool had nothing checkable at all.
    """

    def test_go_report_against_a_go_test_run(self, go_issue, go_test_panic):
        text = go_issue.full_text
        expected = expected_signature(extract_signals(text), text)
        observed = observed_signature(go_test_panic, expected.exception_type)
        match = compare_signatures(expected, observed)
        assert expected.exception_type == "panic"
        assert expected.exception_message == "runtime error: integer divide by zero"
        assert match.exception == "match"
        # The reporter ran the binary from /home/dana and the reproduction ran
        # the tests from /tmp. Only the function and the basename are compared.
        assert match.frames == "match"
        assert match.matched_frame.startswith("applyRate ")
        assert verdict_from_match(match) == "reproduced"

    def test_same_panic_in_a_different_function_is_a_mismatch(self, go_test_panic):
        """The case the panic line alone cannot tell apart, and the reason
        the frame comparison is worth having for these languages."""
        text = (
            "```\n"
            "panic: runtime error: integer divide by zero\n"
            "\n"
            "goroutine 1 [running]:\n"
            "example.com/shop/tax.perUnit(...)\n"
            "\t/home/dana/src/shop/tax/tax.go:22\n"
            "main.main()\n"
            "\t/home/dana/src/shop/cmd/shop/main.go:11 +0x124\n"
            "```\n"
        )
        expected = expected_signature(extract_signals(text), text)
        observed = observed_signature(go_test_panic, expected.exception_type)
        match = compare_signatures(expected, observed)
        assert match.exception == "match"
        assert match.frames == "mismatch"
        assert verdict_from_match(match) == "partial"
        assert any("perUnit" in note and "applyRate" in note for note in match.notes)

    def test_rust_report_with_a_backtrace_matches_on_the_function(self, rust_test_backtrace):
        text = "```\n" + rust_test_backtrace + "```\n"
        expected = expected_signature(extract_signals(text), text)
        observed = observed_signature(rust_test_backtrace, expected.exception_type)
        match = compare_signatures(expected, observed)
        assert expected.exception_type == "panic"
        assert match.frames == "match"
        assert match.matched_frame.startswith("apply_rate ")
        assert verdict_from_match(match) == "reproduced"

    def test_rust_report_without_a_backtrace_is_unknown_not_a_match(
        self, rust_issue, rust_test_backtrace
    ):
        """A bare panic header names a file and a line but no function, and
        naming the same function is the point. `unknown` is the honest answer
        and it is not evidence in either direction."""
        text = rust_issue.full_text
        expected = expected_signature(extract_signals(text), text)
        observed = observed_signature(rust_test_backtrace, expected.exception_type)
        match = compare_signatures(expected, observed)
        assert expected.frames[-1].symbol is None
        assert match.exception == "match"
        assert match.message == "exact"
        assert match.frames == "unknown"

    def test_rust_panic_line_is_read_across_two_lines(self, rust_test_backtrace):
        """Rust puts the location on the panic line and the message on the
        next one, so no single line of the output is a failure on its own."""
        observed = observed_signature(rust_test_backtrace)
        assert observed.exception_type == "panic"
        assert observed.exception_message == "attempt to divide by zero"
        assert "rust panic line in the output" in observed.sources

    def test_observed_frames_names_the_language_that_produced_them(self, go_test_panic):
        frames, source = observed_frames(go_test_panic)
        assert source == "go panic frames in the output"
        assert frames[-1].symbol == "applyRate"

    def test_a_go_panic_does_not_outrank_a_pytest_failure(self, go_test_panic):
        """Ordering is unchanged: pytest's own body still wins when both are
        present, which is the case a polyglot repository produces."""
        frames, source = observed_frames(PYTEST_OUTPUT + "\n" + go_test_panic)
        assert source == "pytest traceback frames"


class TestJvmSignatures:
    """The JVM half, run against real OpenJDK 26 and JUnit 5 output.

    Unlike Go and Rust, the JVM names a real exception class, so these
    signatures carry a type and a message as well as frames.
    """

    def test_the_exception_line_is_read_from_a_caused_by_chain(self, jvm_caused_by):
        """The header the JVM actually prints begins with `Exception in
        thread "main" `, which the anchored line scanner cannot match. Before
        this feature the type came back None for every real JVM failure."""
        signature = observed_signature(jvm_caused_by)
        assert signature.exception_type == "java.lang.NullPointerException"
        assert signature.exception_message is not None
        assert signature.exception_message.startswith("Cannot invoke")
        assert "jvm exception line in the output" in signature.sources

    def test_the_root_cause_not_the_wrapper_is_reported(self, jvm_caused_by):
        """`IllegalStateException: checkout failed` is the rethrow in the
        catch block; the NullPointerException under it is the bug."""
        signature = observed_signature(jvm_caused_by)
        assert signature.exception_type != "java.lang.IllegalStateException"
        assert signature.frames[-1].symbol == "applyCoupon"
        assert signature.frames[-1].path == "Pricing.java"

    def test_a_jvm_exception_is_not_called_a_panic(self, jvm_caused_by):
        """Go and Rust panic; the JVM throws. Naming it a panic in the
        evidence a reader checks the verdict against would be a small lie."""
        signature = observed_signature(jvm_caused_by)
        assert "jvm exception frames in the output" in signature.sources
        assert not any("panic" in source for source in signature.sources)

    def test_junit_failing_test_names_are_read(self, jvm_junit_assertion):
        signature = observed_signature(jvm_junit_assertion)
        assert signature.tests == ["TaxTest::couponIsSubtractedFromTheTotal"]
        assert "JUnit failure lines" in signature.sources

    def test_an_issue_pasting_a_jvm_trace_gets_a_full_signature(self, jvm_caused_by):
        text = f"The checkout crashes.\n\n```\n{jvm_caused_by}```\n"
        signature = expected_signature(extract_signals(text), text)
        assert signature.exception_type == "java.lang.NullPointerException"
        assert signature.frames[-1].symbol == "applyCoupon"
        assert not signature.is_empty

    def test_a_reproduced_jvm_bug_reads_as_reproduced(self, jvm_caused_by):
        text = f"```\n{jvm_caused_by}```\n"
        expected = expected_signature(extract_signals(text), text)
        observed = observed_signature(jvm_caused_by, expected.exception_type)
        match = compare_signatures(expected, observed)
        assert match.exception == "match"
        assert match.frames == "match"
        assert verdict_from_match(match) == "reproduced"

    def test_two_unrelated_assertion_failures_do_not_match(self, jvm_junit_assertion):
        """THE FALSE-MATCH CASE, and the reason the harness filter exists.
        Both of these are a failed assertEquals, so both are built by the
        same six frames of JUnit machinery. Only the test method below that
        machinery tells them apart."""
        other = jvm_junit_assertion.replace(
            "com.example.shop.TaxTest.couponIsSubtractedFromTheTotal(TaxTest.java:16)",
            "com.example.shop.ShippingTest.heavyParcelsCostMore(ShippingTest.java:11)",
        )
        left, _ = observed_frames(jvm_junit_assertion)
        right, _ = observed_frames(other)
        assert left[-1].symbol == "couponIsSubtractedFromTheTotal"
        assert right[-1].symbol == "heavyParcelsCostMore"
        assert compare_frames(left, right)[0] == "mismatch"


class TestQualifiedExceptionNames:
    """A fully qualified class name and its simple name are the same class."""

    def test_a_simple_name_matches_a_qualified_one(self):
        """A reporter writes `NullPointerException`; the runtime prints
        `java.lang.NullPointerException`. Calling that a MISMATCH would be
        worse than the `unknown` this feature replaces, because a mismatch
        is positive evidence against a reproduction."""
        expected = FailureSignature(exception_type="NullPointerException")
        observed = FailureSignature(exception_type="java.lang.NullPointerException")
        assert compare_signatures(expected, observed).exception == "match"

    def test_two_different_qualified_names_still_mismatch(self):
        """When BOTH sides carry a package, a difference is a real one: two
        packages may each define a `ValueError`."""
        expected = FailureSignature(exception_type="com.example.a.ValueError")
        observed = FailureSignature(exception_type="com.example.b.ValueError")
        assert compare_signatures(expected, observed).exception == "mismatch"

    def test_two_different_simple_names_still_mismatch(self):
        expected = FailureSignature(exception_type="IllegalStateException")
        observed = FailureSignature(exception_type="NullPointerException")
        assert compare_signatures(expected, observed).exception == "mismatch"

    def test_the_rule_helps_python_too(self):
        """This was never a JVM-only problem: a Python reporter writes
        `HTTPError` where the traceback says
        `requests.exceptions.HTTPError`."""
        expected = FailureSignature(exception_type="HTTPError")
        observed = FailureSignature(exception_type="requests.exceptions.HTTPError")
        assert compare_signatures(expected, observed).exception == "match"
