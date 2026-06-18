"""Tests for github.py."""

import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import github


class _Stream:
    def __init__(self, data: bytes = b"", block: bool = False):
        self._data = data
        self._block = block

    def read(self, n: int = -1) -> bytes:
        if self._block:
            time.sleep(5)  # longer than any patched timeout; thread is a daemon
            return b""
        if n is None or n < 0:
            return self._data
        return self._data[:n]


class _FakePopen:
    """Minimal Popen double for the streaming, byte-capped `_gh`."""

    def __init__(self, stdout: bytes = b"", returncode: int = 0,
                 stderr: bytes = b"", block: bool = False):
        self.stdout = _Stream(stdout, block=block)
        self.stderr = _Stream(stderr)
        self.returncode = returncode

    def kill(self) -> None:
        pass

    def wait(self, **_kwargs) -> int:
        return self.returncode


class TestGithubSubprocess(unittest.TestCase):
    @mock.patch("shutil.which")
    def test_gh_missing(self, mock_which):
        mock_which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "the GitHub CLI `gh` is not installed"):
            github._gh("pr", "view")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.Popen")
    def test_gh_failure(self, mock_popen, mock_which):
        mock_which.return_value = "/usr/bin/gh"
        mock_popen.return_value = _FakePopen(b"", returncode=1, stderr=b"some gh error with token ghp_123456789012345678901234567890")

        with self.assertRaisesRegex(RuntimeError, r"gh pr view failed: some gh error with token \[REDACTED:github_token\]"):
            github._gh("pr", "view")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.Popen")
    def test_gh_success(self, mock_popen, mock_which):
        mock_which.return_value = "/usr/bin/gh"
        mock_popen.return_value = _FakePopen(b"gh output\n", returncode=0)

        result = github._gh("pr", "view")
        self.assertEqual(result, "gh output\n")
        mock_popen.assert_called_once_with(
            ["gh", "pr", "view"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @mock.patch("shutil.which")
    @mock.patch("subprocess.Popen")
    def test_gh_output_over_cap_rejected(self, mock_popen, mock_which):
        # A huge gh stdout (e.g. a hostile PR diff) must be rejected, not OOM
        # (audit 2026-06-13 r3).
        mock_which.return_value = "/usr/bin/gh"
        big = b"x" * (github._GH_MAX_OUTPUT_BYTES + 10)
        mock_popen.return_value = _FakePopen(big, returncode=0)
        with self.assertRaisesRegex(RuntimeError, "output exceeds"):
            github._gh("pr", "diff")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.Popen")
    def test_gh_timeout_raises_clear_error(self, mock_popen, mock_which):
        # #246: a hung gh call must not block forever — it's bounded and surfaces
        # an actionable timeout error rather than hanging.
        mock_which.return_value = "/usr/bin/gh"
        mock_popen.return_value = _FakePopen(b"", returncode=0, block=True)
        with (
            mock.patch.object(github, "_GH_TIMEOUT_S", 0.2),
            self.assertRaisesRegex(RuntimeError, r"gh pr diff timed out after"),
        ):
            github._gh("pr", "diff")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_gh_with_input_timeout_raises_clear_error(self, mock_run, mock_which):
        import subprocess as _sp

        mock_which.return_value = "/usr/bin/gh"
        mock_run.side_effect = _sp.TimeoutExpired(cmd="gh", timeout=github._GH_TIMEOUT_S)
        with self.assertRaisesRegex(RuntimeError, r"timed out after \d+s"):
            github._gh_with_input(["api", "-X", "POST"], '{"body": "x"}')


class CommentBodyTest(unittest.TestCase):
    def test_strips_html_comments_so_markers_cannot_be_forged(self):
        # A finding claim embedding the jury's hidden inline markers must not
        # appear in the body (audit 2026-06-13 r3/N-3).
        from ai_jury.findings import Finding

        f = Finding(
            severity="major",
            file="a.py",
            line=1,
            claim="bug <!-- arc-inline --><!-- arc-sig:deadbeefdeadbeef --> here",
        )
        body = github._comment_body(f)
        self.assertEqual(body.count("arc-inline"), 1)
        self.assertNotIn("arc-sig:deadbeefdeadbeef", body)


class IncrementalMarkerTrustTest(unittest.TestCase):
    @mock.patch("ai_jury.github._gh")
    def test_comment_bodies_filter_to_trusted_authors(self, mock_gh):
        # The reviewed-sha marker is security-sensitive: only trusted authors'
        # comments may be returned, so a fork-PR attacker can't forge it
        # (audit 2026-06-13 r4/M-1).
        mock_gh.return_value = "<!-- arc-reviewed-sha:abc1234 -->"
        github.pr_comment_bodies("1")
        args = mock_gh.call_args.args
        jq = args[args.index("--jq") + 1]
        self.assertIn("authorAssociation", jq)
        for assoc in ("OWNER", "MEMBER", "COLLABORATOR"):
            self.assertIn(assoc, jq)
        self.assertNotIn("CONTRIBUTOR", jq)


class TestGithubFunctions(unittest.TestCase):
    @mock.patch("ai_jury.github._gh")
    def test_pr_diff(self, mock_gh):
        mock_gh.return_value = "diff output"
        result = github.pr_diff("123")
        self.assertEqual(result, "diff output")
        mock_gh.assert_called_once_with("pr", "diff", "--", "123")

    @mock.patch("ai_jury.github._gh")
    def test_pr_diff_with_repo(self, mock_gh):
        mock_gh.return_value = "diff output"
        result = github.pr_diff("123", repo="org/repo")
        self.assertEqual(result, "diff output")
        mock_gh.assert_called_once_with("pr", "diff", "--repo", "org/repo", "--", "123")

    @mock.patch("ai_jury.github._gh")
    def test_pr_context_success(self, mock_gh):
        mock_gh.return_value = "title\n\nbody\n"
        result = github.pr_context("123")
        self.assertEqual(result, "title\n\nbody")
        mock_gh.assert_called_once()

    @mock.patch("ai_jury.github._gh")
    def test_pr_context_failure(self, mock_gh):
        mock_gh.side_effect = RuntimeError("error")
        result = github.pr_context("123")
        self.assertEqual(result, "")

    @mock.patch("ai_jury.github._gh")
    def test_post_pr_comment(self, mock_gh):
        github.post_pr_comment("123", "my comment", repo="org/repo")
        mock_gh.assert_called_once_with(
            "pr", "comment", "--body", "my comment", "--repo", "org/repo", "--", "123"
        )

    @mock.patch("ai_jury.github._gh")
    def test_pr_head_sha_success(self, mock_gh):
        mock_gh.return_value = "abcdef\n"
        result = github.pr_head_sha("123", repo="org/repo")
        self.assertEqual(result, "abcdef")
        mock_gh.assert_called_once()

    @mock.patch("ai_jury.github._gh")
    def test_pr_head_sha_failure(self, mock_gh):
        mock_gh.side_effect = RuntimeError("error")
        result = github.pr_head_sha("123")
        self.assertEqual(result, "")

    @mock.patch("ai_jury.github._gh")
    def test_pr_comment_bodies_success(self, mock_gh):
        mock_gh.return_value = "comment 1\ncomment 2\n"
        result = github.pr_comment_bodies("123")
        self.assertEqual(result, ["comment 1", "comment 2"])

    @mock.patch("ai_jury.github._gh")
    def test_pr_comment_bodies_failure(self, mock_gh):
        mock_gh.side_effect = RuntimeError("error")
        result = github.pr_comment_bodies("123")
        self.assertEqual(result, [])


class TestGithubWithInput(unittest.TestCase):
    @mock.patch("shutil.which")
    def test_gh_with_input_missing(self, mock_which):
        mock_which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "the GitHub CLI `gh` is not installed"):
            github._gh_with_input(["pr", "comment"], "data")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_gh_with_input_failure(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/gh"

        mock_proc = mock.Mock()
        mock_proc.returncode = 1
        mock_proc.stderr = "some error with AKIAIOSFODNN7EXAMPLE"
        mock_run.return_value = mock_proc

        with self.assertRaisesRegex(RuntimeError, r"gh pr comment failed: some error with \[REDACTED:aws_access_key\]"):
            github._gh_with_input(["pr", "comment"], "data")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_gh_with_input_success(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/gh"

        mock_proc = mock.Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "success\n"
        mock_run.return_value = mock_proc

        result = github._gh_with_input(["pr", "comment"], "data")
        self.assertEqual(result, "success\n")
        mock_run.assert_called_once_with(
            ["gh", "pr", "comment"],
            input="data",
            capture_output=True,
            text=True,
            timeout=github._GH_TIMEOUT_S,
        )


if __name__ == "__main__":
    unittest.main()
