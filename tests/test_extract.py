from issue2repro.extract import (
    extract_commands,
    extract_filenames,
    extract_go_traces,
    extract_jvm_traces,
    extract_node_traces,
    extract_python_traces,
    extract_rust_traces,
    extract_signals,
    outermost_first,
)


class TestPythonTraces:
    def test_frames_and_error(self, python_issue):
        traces = extract_python_traces(python_issue.full_text)
        assert len(traces) == 1
        trace = traces[0]
        assert trace.language == "python"
        assert [f.path for f in trace.frames] == [
            "tests/test_parser.py",
            "/home/alice/project/src/mypkg/parser.py",
        ]
        assert trace.frames[0].line == 9
        assert trace.frames[1].symbol == "parse"
        assert trace.error is not None
        assert trace.error.startswith("UnicodeDecodeError")

    def test_multiple_tracebacks(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "a.py", line 1, in main\n'
            "ValueError: one\n"
            "some prose\n"
            "Traceback (most recent call last):\n"
            '  File "b.py", line 2, in run\n'
            "KeyError: 'two'\n"
        )
        traces = extract_python_traces(text)
        assert len(traces) == 2
        assert traces[0].error == "ValueError: one"
        assert traces[1].error == "KeyError: 'two'"

    def test_no_trace_in_prose(self, vague_issue):
        assert extract_python_traces(vague_issue.full_text) == []

    def test_anchor_lines_do_not_cut_the_trace_short(self):
        """Python 3.11 and later underline the failing expression with a
        "~~~^^^" line. Every frame after the first one has to survive it."""
        text = (
            "Traceback (most recent call last):\n"
            '  File "/tmp/repro.py", line 6, in <module>\n'
            '    widen("-")\n'
            "    ~~~~~^^^^^\n"
            '  File "/tmp/repro.py", line 2, in widen\n'
            "    raise ValueError(\"invalid literal for int() with base 10: '-'\")\n"
            "ValueError: invalid literal for int() with base 10: '-'\n"
        )
        trace = extract_python_traces(text)[0]
        assert [f.symbol for f in trace.frames] == ["<module>", "widen"]
        assert trace.error == "ValueError: invalid literal for int() with base 10: '-'"


class TestNodeTraces:
    def test_frames_error_and_internal_paths(self, node_issue):
        traces = extract_node_traces(node_issue.full_text)
        assert len(traces) == 1
        trace = traces[0]
        assert trace.language == "node"
        assert trace.error is not None
        assert trace.error.startswith("TypeError")
        paths = [f.path for f in trace.frames]
        assert "/Users/bob/app/src/config.js" in paths
        assert "/Users/bob/app/src/index.js" in paths
        assert "node:internal/modules/cjs/loader" in paths
        assert trace.frames[0].symbol == "loadConfig"
        assert trace.frames[0].line == 17

    def test_frame_without_symbol(self):
        text = "Error: boom\n    at /app/lib/util.js:3:1\n"
        traces = extract_node_traces(text)
        assert len(traces) == 1
        assert traces[0].frames[0].symbol is None
        assert traces[0].error == "Error: boom"


class TestCommands:
    def test_bash_block_all_lines(self, python_issue):
        cmds = extract_commands(python_issue.full_text)
        assert cmds == ["pip install -e .", "python -m pytest tests/test_parser.py -x"]

    def test_console_block_dollar_prefixed_only(self, node_issue):
        cmds = extract_commands(node_issue.full_text)
        assert cmds == ["npm install", "npm test"]

    def test_untagged_block_needs_known_head(self):
        text = "```\npytest -k thing\nsome output line\n```\n"
        assert extract_commands(text) == ["pytest -k thing"]

    def test_python_code_block_ignored(self):
        text = "```python\nimport os\nos.remove('x')\n```\n"
        assert extract_commands(text) == []

    def test_comments_and_blanks_skipped(self):
        text = "```bash\n# setup\n\npip install -e .\n```\n"
        assert extract_commands(text) == ["pip install -e ."]

    def test_deduplicates(self):
        text = "```bash\nnpm test\n```\nand again\n```bash\nnpm test\n```\n"
        assert extract_commands(text) == ["npm test"]

    def test_env_prefix_command(self):
        text = "```\nDEBUG=1 node src/index.js\n```\n"
        assert extract_commands(text) == ["DEBUG=1 node src/index.js"]


class TestFilenames:
    def test_finds_paths_and_dedupes(self, python_issue):
        names = extract_filenames(python_issue.full_text)
        assert "tests/test_parser.py" in names
        assert "src/mypkg/decoder.py" in names
        assert len(names) == len(set(names))

    def test_urls_excluded(self):
        text = "see https://example.com/docs/setup.py and local conf.py\n"
        names = extract_filenames(text)
        assert names == ["conf.py"]


class TestSignals:
    def test_vague_issue_has_nothing(self, vague_issue):
        signals = extract_signals(vague_issue.full_text)
        assert signals.traces == []
        assert signals.commands == []
        assert signals.filenames == []

    def test_python_issue_has_everything(self, python_issue):
        signals = extract_signals(python_issue.full_text)
        assert signals.traces
        assert signals.commands
        assert signals.filenames
        assert any("parse()" in ref for ref in signals.code_refs)


class TestGoTraces:
    def test_frames_error_and_order(self, go_issue):
        traces = extract_go_traces(go_issue.full_text)
        assert len(traces) == 1
        trace = traces[0]
        assert trace.language == "go"
        assert trace.error == "panic: runtime error: integer divide by zero"
        # Go prints innermost first; the extractor keeps that order and
        # outermost_first is what normalizes it for the comparison.
        assert [f.symbol for f in trace.frames] == ["applyRate", "Discount", "main"]
        assert trace.frames[0].path == "/home/dana/src/shop/pricing/pricing.go"
        assert trace.frames[0].line == 10
        assert [f.symbol for f in outermost_first(trace)][-1] == "applyRate"

    def test_runtime_and_harness_frames_are_dropped(self, go_test_panic):
        """The frame that failed sits UNDER three harness frames in every
        `go test` panic. Without the filter the innermost frame of any two
        unrelated Go test failures is the same `testing.tRunner.func1.2`,
        so they would compare as a match."""
        trace = extract_go_traces(go_test_panic)[0]
        symbols = [f.symbol for f in trace.frames]
        assert symbols == ["applyRate", "Discount", "TestDiscountUnknownCode"]
        assert not any("testing" in (f.path or "") for f in trace.frames)
        assert outermost_first(trace)[-1].symbol == "applyRate"

    def test_package_path_is_dropped_from_the_symbol(self, go_test_panic):
        """`example.com/shop/pricing.applyRate` compares as `applyRate`, the
        same way a path compares as its basename: the reporter's module path
        and the reproduction's need not agree."""
        trace = extract_go_traces(go_test_panic)[0]
        assert trace.frames[0].symbol == "applyRate"

    def test_fatal_error_is_recognized(self):
        text = (
            "fatal error: concurrent map writes\n"
            "\n"
            "goroutine 7 [running]:\n"
            "example.com/app/store.Put(...)\n"
            "\t/src/app/store/store.go:41\n"
        )
        trace = extract_go_traces(text)[0]
        assert trace.error == "fatal error: concurrent map writes"
        assert trace.frames[0].symbol == "Put"

    def test_only_the_panicking_goroutine_is_read(self):
        """A Go panic dumps every other goroutine too. Reading them would put
        an unrelated frame last, which is the one frame that gets compared."""
        text = (
            "panic: boom\n"
            "\n"
            "goroutine 1 [running]:\n"
            "example.com/app/a.Fail(...)\n"
            "\t/src/app/a/a.go:3\n"
            "\n"
            "goroutine 9 [sleep]:\n"
            "example.com/app/b.Wait(...)\n"
            "\t/src/app/b/b.go:12\n"
        )
        trace = extract_go_traces(text)[0]
        assert [f.symbol for f in trace.frames] == ["Fail"]

    def test_prose_starting_with_panic_is_not_a_trace(self):
        text = "panic: the app panics on startup and I do not know why\n\nAny ideas?\n"
        assert extract_go_traces(text) == []

    def test_no_go_trace_in_a_python_issue(self, python_issue):
        assert extract_go_traces(python_issue.full_text) == []

    def test_a_t_errorf_location_is_not_read_as_a_frame(self, go_test_panic):
        """The fixture holds both kinds of Go failure. The second package
        fails through `t.Errorf`, which reports `tax_test.go:7: got 107,
        want 110`: a failure location, not a stack. It is deliberately not
        read as a frame, because the shape is close enough to ordinary
        prose that matching it would cost more in false frames than it
        returns. The README says so and this pins it."""
        assert "tax_test.go:7:" in go_test_panic
        traces = extract_go_traces(go_test_panic)
        assert len(traces) == 1
        assert not any("tax" in f.path for f in traces[0].frames)


class TestRustTraces:
    def test_panic_header_alone_locates_the_failure(self, rust_issue):
        """The common case: a report pasted without RUST_BACKTRACE=1. The
        header gives a file and a line but names no function, so the frame
        is recorded without a symbol rather than guessed at."""
        traces = extract_rust_traces(rust_issue.full_text)
        assert len(traces) == 1
        trace = traces[0]
        assert trace.language == "rust"
        assert trace.error == "panic: attempt to divide by zero"
        assert len(trace.frames) == 1
        assert trace.frames[0].path == "src/main.rs"
        assert trace.frames[0].line == 5
        assert trace.frames[0].symbol is None

    def test_backtrace_frames_win_over_the_header(self, rust_test_backtrace):
        trace = extract_rust_traces(rust_test_backtrace)[0]
        assert [f.symbol for f in trace.frames] == [
            "apply_rate",
            "discount",
            "discounts_unknown_code",
            "discounts_unknown_code",
        ]
        assert outermost_first(trace)[-1].symbol == "apply_rate"
        assert trace.error == "panic: attempt to divide by zero"

    def test_panic_machinery_and_std_frames_are_dropped(self, rust_test_backtrace):
        """Frames 0 to 2 carry no location at all, and frame 7 is in rustlib.
        None of them is the user's code."""
        trace = extract_rust_traces(rust_test_backtrace)[0]
        assert not any("rustlib" in f.path for f in trace.frames)
        assert not any((f.symbol or "").startswith("core") for f in trace.frames)

    def test_closure_suffix_is_stripped_from_the_symbol(self, rust_test_backtrace):
        trace = extract_rust_traces(rust_test_backtrace)[0]
        # `shop::tests::discounts_unknown_code::{closure#0}` names the test,
        # not a function called `{closure#0}`.
        assert "{closure#0}" not in [f.symbol for f in trace.frames]

    def test_pre_1_73_panic_format(self):
        text = "thread 'main' panicked at 'index out of bounds', src/lib.rs:42:9\n"
        trace = extract_rust_traces(text)[0]
        assert trace.error == "panic: index out of bounds"
        assert trace.frames[0].path == "src/lib.rs"
        assert trace.frames[0].line == 42

    def test_thread_id_in_parentheses_is_tolerated(self):
        """rustc 1.87 and newer print a thread id the older format did not."""
        text = "thread 'main' (96837069) panicked at src/main.rs:5:14:\nattempt to divide by zero\n"
        trace = extract_rust_traces(text)[0]
        assert trace.frames[0].line == 5
        assert trace.error == "panic: attempt to divide by zero"

    def test_rust_backtrace_is_not_read_as_a_node_stack(self, rust_test_backtrace):
        """Rust's `             at ./src/main.rs:5:14` is indistinguishable
        from V8's symbol-less frame if you look at the line alone. Before this
        was fixed, a Rust panic parsed as a Node stack whose innermost frame
        was whatever std function the backtrace ended on."""
        assert extract_node_traces(rust_test_backtrace) == []
        languages = [t.language for t in extract_signals(rust_test_backtrace).traces]
        assert languages == ["rust"]

    def test_a_real_node_stack_still_parses(self):
        """The Rust guard must not cost the Node extractor anything."""
        text = "Error: boom\n    at load (/app/src/index.js:4:11)\n    at /app/src/main.js:9:3\n"
        trace = extract_node_traces(text)[0]
        assert [f.symbol for f in trace.frames] == ["load", None]


class TestJvmTraces:
    def test_an_uncaught_exception_is_read(self):
        """The `Exception in thread "main"` header is the ordinary shape, and
        it is exactly the one the line scanner cannot see, because its
        pattern is anchored at column zero and this line begins with prose."""
        text = (
            'Exception in thread "main" java.lang.IllegalArgumentException: bad rate\n'
            "\tat com.example.shop.Pricing.applyRate(Pricing.java:22)\n"
            "\tat com.example.shop.Checkout.main(Checkout.java:8)\n"
        )
        trace = extract_jvm_traces(text)[0]
        assert trace.language == "jvm"
        assert trace.error == "java.lang.IllegalArgumentException: bad rate"
        assert [f.symbol for f in trace.frames] == ["applyRate", "main"]
        assert trace.frames[0].path == "Pricing.java"
        assert trace.frames[0].line == 22
        # The JVM prints innermost first, so normalizing puts the frame that
        # raised last, the same as every other language.
        assert outermost_first(trace)[-1].symbol == "applyRate"

    def test_the_root_cause_wins_not_the_wrapper(self, jvm_caused_by):
        """A rethrow in a catch block is the handler, not the bug. Every
        caller takes the LAST trace, so emitting one trace per link puts the
        deepest cause where the comparison will look."""
        traces = extract_jvm_traces(jvm_caused_by)
        assert [t.error.split(":")[0] for t in traces] == [
            "java.lang.IllegalStateException",
            "java.lang.NullPointerException",
        ]
        assert outermost_first(traces[-1])[-1].symbol == "applyCoupon"
        assert outermost_first(traces[-1])[-1].path == "Pricing.java"

    def test_the_more_marker_ends_a_cause(self, jvm_caused_by):
        """`... 1 more` truncates the shared tail of the cause's stack. It
        terminates the frame list rather than being read as one."""
        cause = extract_jvm_traces(jvm_caused_by)[-1]
        assert [f.line for f in cause.frames] == [13, 9]
        assert "... 1 more" in jvm_caused_by

    def test_assertion_machinery_and_reflection_frames_are_dropped(self, jvm_junit_assertion):
        """THE FILTER IS LOAD BEARING. JUnit builds the error inside its own
        assertion machinery, so six `org.junit.jupiter.api` frames sit above
        the test method and four JDK reflection frames sit below it. Compared
        as printed, the innermost frame of every failed assertEquals in the
        world is AssertionFailureBuilder.build, so two unrelated failures
        would agree and score a match."""
        trace = extract_jvm_traces(jvm_junit_assertion)[-1]
        assert [f.symbol for f in trace.frames] == ["couponIsSubtractedFromTheTotal"]
        assert not any("junit" in f.path.lower() for f in trace.frames)
        assert not any(f.path == "Method.java" for f in trace.frames)
        # The machinery really is present in the fixture; this is a filter,
        # not an input that happened to be clean.
        assert "AssertionFailureBuilder.build" in jvm_junit_assertion
        assert "java.base/java.lang.reflect.Method.invoke" in jvm_junit_assertion

    def test_junit_frames_have_no_at_keyword(self, jvm_junit_assertion):
        """Found by running the console launcher rather than assumed: it
        prints frames indented under the `=>` line with no `at`, which is the
        one JVM shape where the `File.java:13` location is the only thing
        separating a frame from prose."""
        assert "       com.example.shop.TaxTest.couponIsSubtractedFromTheTotal(" in (
            jvm_junit_assertion
        )
        assert "\tat com.example.shop.TaxTest" not in jvm_junit_assertion
        trace = extract_jvm_traces(jvm_junit_assertion)[-1]
        assert trace.frames and trace.frames[0].line == 16

    def test_the_arrow_header_is_read(self, jvm_junit_assertion):
        """JUnit heads the exception with `=> `, so the type is on a line the
        anchored scanner cannot match either."""
        trace = extract_jvm_traces(jvm_junit_assertion)[-1]
        assert trace.error == "org.opentest4j.AssertionFailedError: expected: <910> but was: <900>"

    def test_a_frame_with_no_source_location_is_skipped(self):
        """`(Native Method)` and `(Unknown Source)` are frames with nowhere to
        point. They are dropped rather than recorded with a made-up site."""
        text = (
            "java.lang.RuntimeException: boom\n"
            "\tat com.example.Native.call(Native Method)\n"
            "\tat com.example.Gen.run(Unknown Source)\n"
            "\tat com.example.Real.work(Real.java:7)\n"
        )
        trace = extract_jvm_traces(text)[0]
        assert [f.symbol for f in trace.frames] == ["work"]

    def test_a_header_with_no_frames_is_not_a_trace(self):
        """Without this rule any prose sentence ending in `Error` opens a
        trace. A header has to be followed by something frame-shaped."""
        assert extract_jvm_traces("I hit a java.lang.IllegalStateException here.\n") == []
        assert extract_jvm_traces("ScaryError: it broke\n\nand then I gave up\n") == []

    def test_a_node_stack_is_not_read_as_a_jvm_trace(self):
        """The same collision the Rust guard exists for. Node writes
        `at fn (path:1:2)` with a space before the paren; the JVM writes
        `at fq(File.java:13)` without one, and the location has to name a
        JVM source file."""
        text = (
            "TypeError: boom\n"
            "    at applyRate (/app/src/pricing.js:14:11)\n"
            "    at checkout (/app/src/checkout.js:8:3)\n"
        )
        assert extract_jvm_traces(text) == []
        assert [t.language for t in extract_signals(text).traces] == ["node"]

    def test_a_python_traceback_is_not_read_as_a_jvm_trace(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "/app/x.py", line 3, in main\n'
            "    go()\n"
            "ValueError: boom\n"
        )
        assert extract_jvm_traces(text) == []

    def test_a_rust_backtrace_is_not_read_as_a_jvm_trace(self, rust_test_backtrace):
        assert extract_jvm_traces(rust_test_backtrace) == []

    def test_a_kotlin_file_is_recognized(self):
        """Kotlin and Scala compile to the same trace format; only the file
        extension differs."""
        text = (
            "kotlin.KotlinNullPointerException: boom\n"
            "\tat com.example.shop.PricingKt.applyRate(Pricing.kt:9)\n"
        )
        trace = extract_jvm_traces(text)[0]
        assert trace.frames[0].path == "Pricing.kt"
        assert trace.frames[0].symbol == "applyRate"

    def test_the_signals_list_includes_jvm_traces(self, jvm_caused_by):
        assert [t.language for t in extract_signals(jvm_caused_by).traces] == [
            "jvm",
            "jvm",
        ]
