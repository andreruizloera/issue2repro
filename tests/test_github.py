import json
from pathlib import Path

import pytest

from issue2repro.github import Issue2ReproError, load_issue_file, parse_issue_url
from issue2repro.models import IssueRef

REF = IssueRef("acme", "widget", 7)


class TestParseIssueUrl:
    def test_full_url(self):
        ref = parse_issue_url("https://github.com/acme/widget/issues/7")
        assert ref == REF

    def test_http_and_www(self):
        assert parse_issue_url("http://www.github.com/acme/widget/issues/7") == REF

    def test_trailing_slash(self):
        assert parse_issue_url("https://github.com/acme/widget/issues/7/") == REF

    def test_anchor_suffix(self):
        assert parse_issue_url("https://github.com/acme/widget/issues/7#issuecomment-1") == REF

    def test_shorthand(self):
        assert parse_issue_url("acme/widget#7") == REF

    def test_repo_with_dots(self):
        ref = parse_issue_url("https://github.com/acme/widget.js/issues/12")
        assert ref.repo == "widget.js"
        assert ref.number == 12

    @pytest.mark.parametrize(
        "bad",
        [
            "https://github.com/acme/widget",
            "https://github.com/acme/widget/pull/7",
            "https://gitlab.com/acme/widget/issues/7",
            "not a url",
            "",
        ],
    )
    def test_invalid_raises(self, bad):
        with pytest.raises(Issue2ReproError):
            parse_issue_url(bad)

    def test_derived_urls(self):
        assert REF.repo_url == "https://github.com/acme/widget"
        assert REF.issue_url == "https://github.com/acme/widget/issues/7"
        assert REF.slug == "acme/widget#7"


class TestLoadIssueFile:
    def test_loads_title_body_comments(self, tmp_path: Path):
        path = tmp_path / "issue.json"
        path.write_text(
            json.dumps(
                {
                    "title": "t",
                    "body": "b",
                    "comments": [{"body": "c1"}, "c2", {"body": ""}],
                }
            )
        )
        issue = load_issue_file(REF, path)
        assert issue.title == "t"
        assert issue.body == "b"
        assert issue.comments == ["c1", "c2"]

    def test_missing_file(self, tmp_path: Path):
        with pytest.raises(Issue2ReproError, match="not found"):
            load_issue_file(REF, tmp_path / "nope.json")

    def test_bad_json(self, tmp_path: Path):
        path = tmp_path / "bad.json"
        path.write_text("{not json")
        with pytest.raises(Issue2ReproError, match="not valid JSON"):
            load_issue_file(REF, path)

    def test_non_object(self, tmp_path: Path):
        path = tmp_path / "list.json"
        path.write_text("[1, 2]")
        with pytest.raises(Issue2ReproError, match="JSON object"):
            load_issue_file(REF, path)

    def test_full_text_joins_parts(self, tmp_path: Path):
        path = tmp_path / "issue.json"
        path.write_text(json.dumps({"title": "t", "body": "b", "comments": ["c"]}))
        issue = load_issue_file(REF, path)
        assert issue.full_text == "t\n\nb\n\nc"
