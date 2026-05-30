"""Tests for inline GitHub review comments (issue #5)."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest import mock

from agent_review_council import cli, github
from agent_review_council.github import build_inline_payload, post_inline_comments
from agent_review_council.findings import Finding


class BuildInlinePayloadTests(unittest.TestCase):
    def test_only_file_and_line_findings(self):
        findings = [
            Finding(severity="major", file="a.py", line=10, claim="c1", suggested_fix="fix1"),
            Finding(severity="minor", file="", line=5, claim="no file"),
            Finding(severity="minor", file="b.py", line=None, claim="no line"),
            Finding(severity="info", file="c.py", line=3, claim="c4"),
        ]
        payload = build_inline_payload(findings)
        self.assertEqual(len(payload), 2)
        self.assertEqual(payload[0]["path"], "a.py")
        self.assertEqual(payload[0]["line"], 10)
        self.assertEqual(payload[0]["side"], "RIGHT")
        self.assertIn("[major]", payload[0]["body"])
        self.assertIn("c1", payload[0]["body"])
        self.assertIn("fix1", payload[0]["body"])

    def test_empty(self):
        self.assertEqual(build_inline_payload([]), [])

    def test_marker_present(self):
        payload = build_inline_payload([Finding(severity="nit", file="x.py", line=1, claim="c")])
        self.assertIn(github.INLINE_MARKER, payload[0]["body"])


class PostInlineDryRunTests(unittest.TestCase):
    def test_dry_run_no_network(self):
        findings = [Finding(severity="major", file="a.py", line=10, claim="c1")]
        buf = io.StringIO()
        with redirect_stdout(buf):
            payload = post_inline_comments("1", findings, dry_run=True)
        self.assertEqual(payload["event"], "COMMENT")
        self.assertEqual(len(payload["comments"]), 1)
        printed = json.loads(buf.getvalue())
        self.assertEqual(printed["comments"][0]["path"], "a.py")
        self.assertEqual(printed["comments"][0]["side"], "RIGHT")


class CliDryRunWiringTests(unittest.TestCase):
    def test_cli_forwards_dry_run_to_post_inline(self):
        """--post-inline --dry-run must pass dry_run=True to post_inline_comments."""
        diff = "diff --git a/a.py b/a.py\n"
        with mock.patch.object(cli, "_read_diff", return_value=(diff, "")), \
                mock.patch.object(cli, "post_inline_comments") as posted:
            rc = cli.main([
                "--diff-file", "x.diff", "--pr", "1", "--repo", "o/r",
                "--mock", "--quiet", "--post-inline", "--dry-run",
            ])
        self.assertEqual(rc, 0)
        self.assertTrue(posted.called)
        self.assertTrue(posted.call_args.kwargs.get("dry_run"))


if __name__ == "__main__":
    unittest.main()
