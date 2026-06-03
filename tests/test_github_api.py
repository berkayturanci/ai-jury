"""Coverage for github.py API / label / inline / progress paths. Network-free
(every `gh` invocation is mocked)."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import github  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402


def _finding(**kw):
    base = {"severity": "major", "file": "a.py", "claim": "c", "line": 2}
    base.update(kw)
    return Finding(**base)


class CompareDiffTests(unittest.TestCase):
    @mock.patch("ai_jury.github._resolve_repo", return_value="o/r")
    @mock.patch("ai_jury.github._gh", return_value="DIFF")
    def test_success(self, mock_gh, _):
        self.assertEqual(github.compare_diff("a", "b", "o/r"), "DIFF")
        mock_gh.assert_called_once_with(
            "api", "-H", "Accept: application/vnd.github.v3.diff",
            "--", "repos/o/r/compare/a...b",
        )

    @mock.patch("ai_jury.github._resolve_repo", return_value="")
    def test_unresolved_repo(self, _):
        self.assertEqual(github.compare_diff("a", "b"), "")

    @mock.patch("ai_jury.github._resolve_repo", return_value="o/r")
    @mock.patch("ai_jury.github._gh", side_effect=RuntimeError("boom"))
    def test_error_returns_empty(self, *_):
        self.assertEqual(github.compare_diff("a", "b", "o/r"), "")


class LabelTests(unittest.TestCase):
    def test_build_label_args_empty(self):
        self.assertEqual(github.build_label_args("1", [], "o/r"), [])
        self.assertEqual(github.build_label_args("1", ["", "  "]), [])

    def test_build_label_args_full(self):
        self.assertEqual(
            github.build_label_args("42", ["a", "b"], "o/r"),
            ["pr", "edit", "--add-label", "a", "--add-label", "b", "--repo", "o/r", "--", "42"],
        )

    @mock.patch("ai_jury.github._gh")
    def test_apply_labels_noop(self, mock_gh):
        self.assertEqual(github.apply_labels("1", []), [])
        mock_gh.assert_not_called()

    @mock.patch("ai_jury.github._gh")
    def test_apply_labels_posts(self, mock_gh):
        args = github.apply_labels("42", ["risk: high"], "o/r")
        self.assertIn("--add-label", args)
        mock_gh.assert_called_once()


class ResolveRepoTests(unittest.TestCase):
    def test_explicit(self):
        self.assertEqual(github._resolve_repo("o/r"), "o/r")

    @mock.patch("ai_jury.github._gh", return_value='{"nameWithOwner": "o/r"}')
    def test_via_gh(self, _):
        self.assertEqual(github._resolve_repo(None), "o/r")

    @mock.patch("ai_jury.github._gh", side_effect=RuntimeError("x"))
    def test_error_empty(self, _):
        self.assertEqual(github._resolve_repo(None), "")


class ExistingInlineKeysTests(unittest.TestCase):
    @mock.patch("ai_jury.github._gh")
    def test_parses_marker_comments(self, mock_gh):
        body = github._comment_body(_finding(file="a.py", line=3))
        mock_gh.return_value = json.dumps([
            {"path": "a.py", "line": 3, "body": body},
            {"path": "b.py", "line": None, "original_line": 9, "body": body},
            {"path": "c.py", "line": 1, "body": "no marker here"},
            "not-a-dict",
        ])
        keys = github._existing_inline_keys("1", "o/r")
        self.assertEqual(len(keys), 2)  # only the two marker comments

    @mock.patch("ai_jury.github._gh", side_effect=RuntimeError("x"))
    def test_error_empty(self, _):
        self.assertEqual(github._existing_inline_keys("1", "o/r"), set())

    @mock.patch("ai_jury.github._gh", return_value='{"not": "a list"}')
    def test_nonlist_empty(self, _):
        self.assertEqual(github._existing_inline_keys("1", "o/r"), set())


class IssueCommentTests(unittest.TestCase):
    @mock.patch("ai_jury.github._gh_with_input", return_value='{"id": 123}')
    def test_create_returns_id(self, _):
        self.assertEqual(github._create_issue_comment("1", "b", "o/r"), 123)

    @mock.patch("ai_jury.github._gh_with_input", side_effect=RuntimeError("x"))
    def test_create_error_none(self, _):
        self.assertIsNone(github._create_issue_comment("1", "b", "o/r"))

    @mock.patch("ai_jury.github._gh_with_input", return_value="{}")
    def test_edit_true(self, _):
        self.assertTrue(github._edit_issue_comment(5, "b", "o/r"))

    @mock.patch("ai_jury.github._gh_with_input", side_effect=RuntimeError("x"))
    def test_edit_false(self, _):
        self.assertFalse(github._edit_issue_comment(5, "b", "o/r"))


class PostInlineCommentsTests(unittest.TestCase):
    @mock.patch("ai_jury.github._gh_with_input")
    @mock.patch("ai_jury.github._existing_inline_keys", return_value=set())
    @mock.patch("ai_jury.github._resolve_repo", return_value="o/r")
    def test_posts_new(self, _repo, _keys, mock_input):
        payload = github.post_inline_comments("1", [_finding()], "o/r")
        self.assertEqual(len(payload["comments"]), 1)
        mock_input.assert_called_once()

    @mock.patch("ai_jury.github._gh_with_input")
    @mock.patch("ai_jury.github._resolve_repo", return_value="o/r")
    def test_dedup_skips_existing(self, _repo, mock_input):
        f = _finding()
        key = ("a.py", 2, github._sig_from_body(github._comment_body(f)))
        with mock.patch("ai_jury.github._existing_inline_keys", return_value={key}):
            payload = github.post_inline_comments("1", [f], "o/r")
        self.assertEqual(payload["comments"], [])
        mock_input.assert_not_called()

    def test_dry_run_prints_without_network(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            payload = github.post_inline_comments("1", [_finding()], "o/r", dry_run=True)
        self.assertEqual(len(payload["comments"]), 1)
        self.assertIn("COMMENT", buf.getvalue())


class ProgressReporterTests(unittest.TestCase):
    @mock.patch("ai_jury.github._edit_issue_comment", return_value=True)
    @mock.patch("ai_jury.github._create_issue_comment", return_value=7)
    @mock.patch("ai_jury.github._resolve_repo", return_value="o/r")
    def test_create_then_edit_then_finish(self, _repo, mock_create, mock_edit):
        r = github.ProgressReporter("1", "o/r")
        r.update("round 1: reviewing")
        mock_create.assert_called_once()
        self.assertEqual(r.comment_id, 7)
        r.update("round 2: debate")
        mock_edit.assert_called_once()
        r.finish("# 🏛️ AI Jury\n\nverdict")
        self.assertEqual(mock_edit.call_count, 2)

    @mock.patch("ai_jury.github._resolve_repo", return_value="")
    def test_no_repo_is_noop(self, _):
        r = github.ProgressReporter("1", None)
        r.update("x")
        r.finish("y")
        self.assertIsNone(r.comment_id)


if __name__ == "__main__":
    unittest.main()
