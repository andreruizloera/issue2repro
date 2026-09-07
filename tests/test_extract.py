from issue2repro.extract import (
    extract_commands,
    extract_filenames,
    extract_node_traces,
    extract_python_traces,
    extract_signals,
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
