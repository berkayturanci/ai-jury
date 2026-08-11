"""`--commit` / `--commits` as input sources (issue #367).

They resolve to a unified diff and flow through the existing pipeline unchanged,
so the tests here cover resolution, the one-source rule, and the revision guard —
not the pipeline, which is already covered for `--diff-file`.
"""

import subprocess
import unittest
import unittest.mock as mock
from types import SimpleNamespace

from ai_jury import cli


def _args(**kw):
    base = {"pr": None, "issue": None, "diff_file": None, "repo": None,
            "commit": None, "commits": None}
    base.update(kw)
    return SimpleNamespace(**base)


def _proc(stdout="", stderr="", code=0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


class TestRevisionGuard(unittest.TestCase):
    """A revision reaches git's argv, so a leading dash is the risk — not quoting."""

    def test_a_normal_revision_passes(self):
        for rev in ("HEAD", "abc1234", "origin/main..HEAD", "HEAD~5..HEAD", "v1.2.3"):
            with self.subTest(rev=rev):
                self.assertEqual(cli._checked_revision(rev, "--commit"), rev)

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertEqual(cli._checked_revision("  HEAD  ", "--commit"), "HEAD")

    def test_a_leading_dash_is_refused(self):
        # git would read it as an option; refused rather than escaped.
        for rev in ("--upload-pack=evil", "-x", "--output=/tmp/x"):
            with self.subTest(rev=rev):
                with self.assertRaises(SystemExit) as ctx:
                    cli._checked_revision(rev, "--commit")
                self.assertIn("may not start with '-'", str(ctx.exception))

    def test_an_empty_revision_is_refused(self):
        # Silently widening the diff is worse than failing.
        for rev in ("", "   ", None):
            with self.subTest(rev=rev):
                with self.assertRaises(SystemExit) as ctx:
                    cli._checked_revision(rev, "--commits")
                self.assertIn("needs a revision", str(ctx.exception))


class TestResolution(unittest.TestCase):
    def test_commit_shows_one_commit_as_a_patch(self):
        with mock.patch("subprocess.run", return_value=_proc("diff --git a b\n")) as run:
            diff, context = cli._read_diff(_args(commit="abc1234"))
        self.assertEqual(context, "")
        self.assertIn("diff --git", diff)
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["git", "show"])
        self.assertIn("abc1234", argv)
        # `--` terminates options, so a revision can never be read as a path/flag.
        self.assertEqual(argv[-1], "--")
        # A merge commit prints no diff without -m; first-parent keeps it reviewable.
        self.assertIn("-m", argv)
        self.assertIn("--first-parent", argv)

    def test_commits_diffs_a_range(self):
        with mock.patch("subprocess.run", return_value=_proc("diff --git a b\n")) as run:
            cli._read_diff(_args(commits="origin/main..HEAD"))
        argv = run.call_args.args[0]
        self.assertEqual(argv[:2], ["git", "diff"])
        self.assertIn("origin/main..HEAD", argv)
        self.assertEqual(argv[-1], "--")

    def test_a_bad_revision_surfaces_gits_own_message(self):
        bad = _proc(stderr="fatal: bad revision 'nope'\n", code=128)
        with mock.patch("subprocess.run", return_value=bad), \
                self.assertRaises(SystemExit) as ctx:
            cli._read_diff(_args(commit="nope"))
        self.assertIn("bad revision", str(ctx.exception))

    def test_an_empty_diff_is_an_error_not_an_empty_review(self):
        # Reviewing nothing and reporting a verdict would be worse than failing.
        with mock.patch("subprocess.run", return_value=_proc("   \n")), \
                self.assertRaises(SystemExit) as ctx:
            cli._read_diff(_args(commits="HEAD..HEAD"))
        self.assertIn("empty diff", str(ctx.exception))

    def test_git_missing_fails_cleanly(self):
        with mock.patch("subprocess.run", side_effect=OSError("no git")), \
                self.assertRaises(SystemExit) as ctx:
            cli._read_diff(_args(commit="HEAD"))
        self.assertIn("could not run git", str(ctx.exception))

    def test_a_timeout_fails_cleanly(self):
        boom = subprocess.TimeoutExpired(cmd="git", timeout=120)
        with mock.patch("subprocess.run", side_effect=boom), \
                self.assertRaises(SystemExit) as ctx:
            cli._read_diff(_args(commit="HEAD"))
        self.assertIn("could not run git", str(ctx.exception))

    def test_the_ingest_cap_applies_to_git_output_too(self):
        huge = "x" * (cli._MAX_DIFF_INGEST_BYTES + 10)
        with mock.patch("subprocess.run", return_value=_proc(huge)), \
                self.assertRaises(SystemExit) as ctx:
            cli._read_diff(_args(commit="HEAD"))
        self.assertIn("ingest limit", str(ctx.exception))

    def test_commit_takes_precedence_over_nothing_else_being_set(self):
        # With no source at all the message names every source.
        with self.assertRaises(SystemExit) as ctx:
            cli._read_diff(_args())
        message = str(ctx.exception)
        for flag in ("--pr", "--issue", "--diff-file", "--commit", "--commits"):
            self.assertIn(flag, message)
