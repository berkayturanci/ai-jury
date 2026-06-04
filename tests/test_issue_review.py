"""Offline tests for issue-quality review mode (issue #221).

`jury --issue N` runs the SAME multi-agent jury machinery (panel -> debate ->
verify -> synthesis) over a GitHub issue's prose with an issue-quality rubric
instead of a code-review one. These tests are deterministic and never touch the
network: `ai_jury.github._gh` (and the CLI's `gh` access points) are mocked.
"""
from __future__ import annotations

import contextlib
import io
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ai_jury import cli, github, prompts  # noqa: E402
from ai_jury.config import DEFAULT_CONFIG, JuryConfig, _from_dict  # noqa: E402
from ai_jury.orchestrator import run_jury  # noqa: E402


def _config() -> JuryConfig:
    return _from_dict(DEFAULT_CONFIG)


def _run_cli(argv):
    """Invoke ``cli.main(argv)`` capturing stdout/stderr and the exit code."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code
    return code, out.getvalue(), err.getvalue()


class IssueBodyTests(unittest.TestCase):
    def test_formats_title_labels_and_body(self):
        with mock.patch.object(github, "_gh") as gh:
            gh.return_value = "# Crash on load\n\n_labels: bug, p1_\n\nIt crashes.\n"
            text = github.issue_body("42")
        self.assertEqual(
            text, "# Crash on load\n\n_labels: bug, p1_\n\nIt crashes."
        )
        # The number must be passed after a `--` separator (security).
        args = gh.call_args.args
        self.assertIn("--", args)
        self.assertEqual(args[args.index("--") + 1], "42")
        self.assertEqual(args[0], "issue")
        self.assertEqual(args[1], "view")

    def test_passes_repo_when_given(self):
        with mock.patch.object(github, "_gh") as gh:
            gh.return_value = "x"
            github.issue_body("7", repo="o/r")
        args = gh.call_args.args
        self.assertIn("--repo", args)
        self.assertEqual(args[args.index("--repo") + 1], "o/r")

    def test_degrades_to_minimal_string_on_failure(self):
        with mock.patch.object(github, "_gh", side_effect=RuntimeError("boom")):
            self.assertEqual(github.issue_body("99"), "# issue #99")


class PostIssueCommentTests(unittest.TestCase):
    def test_uses_gh_issue_comment(self):
        with mock.patch.object(github, "_gh") as gh:
            gh.return_value = ""
            github.post_issue_comment("12", "hello", repo="o/r")
        args = list(gh.call_args.args)
        self.assertEqual(args[0], "issue")
        self.assertEqual(args[1], "comment")
        self.assertIn("--body", args)
        self.assertEqual(args[args.index("--body") + 1], "hello")
        self.assertIn("--repo", args)
        # Number is after the `--` separator.
        self.assertEqual(args[args.index("--") + 1], "12")

    def test_post_issue_comment_without_repo(self):
        with mock.patch.object(github, "_gh") as gh:
            gh.return_value = ""
            github.post_issue_comment("12", "hello")
        args = list(gh.call_args.args)
        self.assertNotIn("--repo", args)
        self.assertEqual(args[args.index("--") + 1], "12")

    def test_cache_key_differs_by_mode(self):
        # The same text under code vs issue mode must not collide in the cache.
        from ai_jury.cache import cache_key
        from ai_jury.config import load_config
        cfg = load_config(None)
        self.assertNotEqual(
            cache_key(cfg, "same text", mode="code"),
            cache_key(cfg, "same text", mode="issue"),
        )


class ForModeTests(unittest.TestCase):
    def test_issue_mode_returns_issue_templates(self):
        t = prompts.for_mode("issue")
        self.assertIs(t["review"], prompts.REVIEW_ISSUE)
        self.assertIs(t["debate"], prompts.DEBATE_ISSUE)
        self.assertIs(t["verify"], prompts.VERIFY_ISSUE)
        self.assertIs(t["synthesis"], prompts.SYNTHESIS_ISSUE)

    def test_code_mode_returns_code_templates(self):
        t = prompts.for_mode("code")
        self.assertIs(t["review"], prompts.REVIEW)
        self.assertIs(t["debate"], prompts.DEBATE)
        self.assertIs(t["verify"], prompts.VERIFY)
        self.assertIs(t["synthesis"], prompts.SYNTHESIS)

    def test_unknown_mode_defaults_to_code(self):
        self.assertIs(prompts.for_mode("anything")["review"], prompts.REVIEW)

    def test_issue_templates_accept_same_format_params(self):
        # The orchestrator calls these with a fixed set of params; they must not
        # raise KeyError, so the call sites stay unchanged across modes.
        prompts.REVIEW_ISSUE.format(
            name="x", context="c", diff="d", policy="p", notice="n"
        )
        prompts.DEBATE_ISSUE.format(
            name="x", diff="d", own_review="o", other_reviews="r", notice="n"
        )
        prompts.VERIFY_ISSUE.format(diff="d", findings="f", context="c", notice="n")
        prompts.SYNTHESIS_ISSUE.format(diff="d", reviews="r", debate="b", notice="n")


class RunJuryIssueModeTests(unittest.TestCase):
    def test_end_to_end_mock(self):
        body = "# Bug\n\n_labels: bug_\n\nSomething breaks but no repro."
        outcome = run_jury(_config(), body, mock=True, seed=1, mode="issue")
        self.assertEqual(len(outcome.reviews), 3)
        self.assertTrue(all(r.ok for r in outcome.reviews))
        self.assertIsNotNone(outcome.synthesis)
        self.assertTrue(outcome.synthesis.ok)

    def test_deterministic_with_seed(self):
        body = "# Bug\n\nrepro unclear"
        a = run_jury(_config(), body, mock=True, seed=7, mode="issue")
        b = run_jury(_config(), body, mock=True, seed=7, mode="issue")
        self.assertEqual(
            [r.output for r in a.reviews], [r.output for r in b.reviews]
        )


class CliIssueTests(unittest.TestCase):
    ISSUE_TEXT = "# Login fails\n\n_labels: bug_\n\nUsers cannot log in."

    def test_review_issue_produces_report_exit_0(self):
        with mock.patch.object(cli, "issue_body", return_value=self.ISSUE_TEXT):
            code, out, _ = _run_cli(["--mock", "--issue", "5", "--quiet"])
        self.assertEqual(code, 0)
        self.assertIn("AI Jury", out)

    def test_post_uses_post_issue_comment(self):
        with mock.patch.object(cli, "issue_body", return_value=self.ISSUE_TEXT), \
             mock.patch.object(cli, "post_issue_comment") as post, \
             mock.patch.object(cli, "post_pr_comment") as pr_post:
            code, _, _ = _run_cli(
                ["--mock", "--issue", "5", "--post", "--quiet"]
            )
        self.assertEqual(code, 0)
        post.assert_called_once()
        pr_post.assert_not_called()
        # Posted to issue #5 with the rendered report body.
        args = post.call_args.args
        self.assertEqual(args[0], "5")
        self.assertIn("AI Jury", args[1])

    def test_pr_only_flags_rejected_with_issue(self):
        for flag in ("--post-inline", "--post-progress", "--label", "--incremental"):
            with mock.patch.object(cli, "issue_body", return_value=self.ISSUE_TEXT):
                code, _, _ = _run_cli(["--mock", "--issue", "5", flag, "--quiet"])
            self.assertIsInstance(code, str)
            self.assertIn(flag, code)

    def test_issue_cannot_combine_with_pr(self):
        code, _, _ = _run_cli(["--mock", "--issue", "5", "--pr", "1", "--quiet"])
        self.assertIsInstance(code, str)
        self.assertIn("--issue", code)

    def test_decision_vote_uses_issue_vocabulary(self):
        # --issue --decision vote now tallies the panel over READY/NEEDS-INFO/
        # UNCLEAR (issue #230) — no more chair fallback, no PR vocabulary.
        with mock.patch.object(cli, "issue_body", return_value=self.ISSUE_TEXT):
            code, out, _ = _run_cli(["--mock", "--issue", "5", "--decision", "vote", "--seed", "1"])
        self.assertEqual(code, 0)
        # The vote block's headline uses the issue vocabulary, not the PR one.
        vote_line = next(ln for ln in out.splitlines() if ln.startswith("**") and "·" in ln)
        self.assertRegex(vote_line, r"\*\*(READY|NEEDS-INFO|UNCLEAR)\*\*")
        self.assertNotIn("request changes", vote_line.lower())
        self.assertNotIn("approve", vote_line.lower())

    def test_config_vote_issue_vocabulary(self):
        import tempfile
        from pathlib import Path
        cfg = Path(tempfile.mkdtemp()) / "jury.toml"
        cfg.write_text('[jury]\nrounds = 1\nchair = "claude"\ndecision = "vote"\n'
                       '\n[[agent]]\nname = "claude"\nvendor = "anthropic"\ncommand = "x"\n')
        with mock.patch.object(cli, "issue_body", return_value=self.ISSUE_TEXT):
            code, out, _ = _run_cli(["--mock", "--issue", "5", "--config", str(cfg), "--seed", "1"])
        self.assertEqual(code, 0)
        self.assertIn("panel vote", out)


class IssueVotingTallyTests(unittest.TestCase):
    def _grp(self, severity, reviewers):
        from ai_jury.consensus import FindingGroup
        from ai_jury.findings import Finding
        f = Finding(severity=severity, file="", claim="gap", reviewer=reviewers[0])
        return FindingGroup(representative=f, reviewers=list(reviewers), severity=severity)

    def test_blocking_gap_needs_info(self):
        from ai_jury import voting
        r = voting.tally_votes([self._grp("major", ["claude", "codex"])],
                               ["claude", "codex"], mode="issue")
        self.assertEqual(r.verdict, voting.NEEDS_INFO)

    def test_minor_gap_unclear(self):
        from ai_jury import voting
        r = voting.tally_votes([self._grp("minor", ["claude"])], ["claude"], mode="issue")
        self.assertEqual(r.verdict, voting.UNCLEAR)

    def test_no_gaps_ready(self):
        from ai_jury import voting
        r = voting.tally_votes([], ["claude", "codex"], mode="issue")
        self.assertEqual(r.verdict, voting.READY)

    def test_tie_breaks_to_strictest(self):
        from ai_jury import voting
        # claude: blocking (NEEDS-INFO) vs codex: clean (READY) -> tie -> NEEDS-INFO.
        r = voting.tally_votes([self._grp("critical", ["claude"])],
                               ["claude", "codex"], mode="issue")
        self.assertEqual(r.verdict, voting.NEEDS_INFO)


if __name__ == "__main__":
    unittest.main()
