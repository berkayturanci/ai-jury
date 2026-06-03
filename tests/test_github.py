"""Tests for github.py."""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import github


class TestGithubSubprocess(unittest.TestCase):
    @mock.patch("shutil.which")
    def test_gh_missing(self, mock_which):
        mock_which.return_value = None
        with self.assertRaisesRegex(RuntimeError, "the GitHub CLI `gh` is not installed"):
            github._gh("pr", "view")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_gh_failure(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/gh"

        mock_proc = mock.Mock()
        mock_proc.returncode = 1
        mock_proc.stderr = "some gh error"
        mock_run.return_value = mock_proc

        with self.assertRaisesRegex(RuntimeError, "gh pr view failed: some gh error"):
            github._gh("pr", "view")

    @mock.patch("shutil.which")
    @mock.patch("subprocess.run")
    def test_gh_success(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/gh"

        mock_proc = mock.Mock()
        mock_proc.returncode = 0
        mock_proc.stdout = "gh output\n"
        mock_run.return_value = mock_proc

        result = github._gh("pr", "view")
        self.assertEqual(result, "gh output\n")
        mock_run.assert_called_once_with(["gh", "pr", "view"], capture_output=True, text=True)


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
        mock_gh.assert_called_once_with("pr", "comment", "--body", "my comment", "--repo", "org/repo", "--", "123")

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
        mock_proc.stderr = "some error"
        mock_run.return_value = mock_proc

        with self.assertRaisesRegex(RuntimeError, "gh pr comment failed: some error"):
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
        mock_run.assert_called_once_with(["gh", "pr", "comment"], input="data", capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
