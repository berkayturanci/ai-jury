"""Tests for the live sticky PR progress comment (issue #125). Network-free."""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import cli  # noqa: E402
from ai_jury.github import PROGRESS_MARKER, render_progress_body  # noqa: E402


class RenderProgressBodyTest(unittest.TestCase):
    def test_in_progress_lists_stages(self):
        body = render_progress_body(["round 1: 4 agents reviewing", "reviewing chunk 2/5"])
        self.assertIn(PROGRESS_MARKER, body)
        self.assertIn("in progress", body)
        self.assertIn("round 1: 4 agents reviewing", body)
        self.assertIn("reviewing chunk 2/5", body)

    def test_finish_becomes_the_report(self):
        body = render_progress_body(["x"], done=True, final="# 🏛️ AI Jury\n\nverdict")
        self.assertIn(PROGRESS_MARKER, body)
        self.assertIn("verdict", body)
        self.assertNotIn("in progress", body)


class MilestoneDetectionTest(unittest.TestCase):
    def test_milestones_match(self):
        for m in (
            "round 1: 4 agents reviewing",
            "round 2: 4 agents cross-examining",
            "reviewing chunk 3/11",
            "verification: chair 'claude' judging 2 candidate findings",
            "synthesis: chair 'claude' consolidating verdict",
            "auto-depth: risk=low → rounds=1, verify=off",
        ):
            self.assertTrue(cli._is_progress_milestone(m), m)

    def test_non_milestones_ignored(self):
        for m in (
            "skipping 'agy': CLI not found (agy)",
            "cached outcome (abc…)",
            "redacted 2 secret(s) before sending to agents",
        ):
            self.assertFalse(cli._is_progress_milestone(m), m)


class ProgressCliTest(unittest.TestCase):
    def test_post_progress_requires_pr(self):
        out, err = io.StringIO(), io.StringIO()
        prev = sys.stdin
        sys.stdin = io.StringIO("diff --git a/x b/x\n@@ -1 +1 @@\n+x\n")
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = cli.main(["--mock", "--diff-file", "-", "--post-progress"])
        except SystemExit as exc:
            code = exc.code
        finally:
            sys.stdin = prev
        self.assertEqual(code, "error: --post-progress requires --pr")


if __name__ == "__main__":
    unittest.main()
