"""Tests for GitHub comment-command parsing (issue #11). Network-free."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.cli import main  # noqa: E402
from ai_jury.commands import (  # noqa: E402
    CommandError,
    parse_comment,
)


class ParseCommentTest(unittest.TestCase):
    def test_basic_review(self):
        cmd = parse_comment("/jury review")
        self.assertEqual(cmd.command, "review")
        self.assertIsNone(cmd.rounds)
        self.assertEqual(cmd.to_cli_args(), [])

    def test_review_with_rounds(self):
        cmd = parse_comment("/jury review --rounds 1")
        self.assertEqual(cmd.rounds, 1)
        self.assertEqual(cmd.to_cli_args(), ["--rounds", "1"])

    def test_rounds_equals_form(self):
        self.assertEqual(parse_comment("/jury review --rounds=2").rounds, 2)

    def test_summary_defaults_to_single_round(self):
        self.assertEqual(parse_comment("/jury summary").to_cli_args(), ["--rounds", "1"])

    def test_command_embedded_in_larger_comment(self):
        text = "Thanks!\n\n/jury review --rounds 2\n\nplease run it"
        self.assertEqual(parse_comment(text).rounds, 2)

    # --- rejection cases ---
    def test_no_trigger_rejected(self):
        with self.assertRaises(CommandError):
            parse_comment("please review this PR")

    def test_unknown_command_rejected(self):
        with self.assertRaises(CommandError):
            parse_comment("/jury deploy")

    def test_unknown_flag_rejected(self):
        with self.assertRaises(CommandError):
            parse_comment("/jury review --post-summary")

    def test_shell_injection_is_not_executed(self):
        # Even though the text contains shell metacharacters, parsing maps to an
        # allowlisted argv and rejects the unknown token — nothing is executed.
        with self.assertRaises(CommandError):
            parse_comment("/jury review; rm -rf /")

    def test_rounds_out_of_range_rejected(self):
        with self.assertRaises(CommandError):
            parse_comment("/jury review --rounds 99")

    def test_rounds_non_integer_rejected(self):
        with self.assertRaises(CommandError):
            parse_comment("/jury review --rounds soon")


class CommentCliModeTest(unittest.TestCase):
    def test_print_args_for_review(self):
        # `jury comment --text ... --print-args` resolves to a safe argv.
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(
                ["comment", "--text", "/jury review --rounds 1", "--pr", "123", "--print-args"]
            )
        self.assertEqual(code, 0)
        printed = out.getvalue().strip()
        self.assertIn("--rounds 1", printed)
        self.assertIn("--pr 123", printed)
        self.assertIn("--post-summary", printed)

    def test_rejected_command_exits_2(self):
        import contextlib
        import io

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["comment", "--text", "/jury deploy", "--print-args"])
        self.assertEqual(code, 2)
        self.assertIn("rejected", err.getvalue())

    def test_no_post_flag_omits_post_summary(self):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["comment", "--text", "/jury review", "--pr", "7", "--no-post", "--print-args"])
        self.assertNotIn("--post-summary", out.getvalue())


if __name__ == "__main__":
    unittest.main()
