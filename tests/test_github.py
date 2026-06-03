import unittest
from unittest.mock import patch, MagicMock
from ai_jury.github import _gh

class TestGitHub(unittest.TestCase):
    def test_gh_not_installed(self):
        with patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "the GitHub CLI `gh` is not installed or not on PATH"):
                _gh("pr", "diff")

    def test_gh_command_failed(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "some error\n"

        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", return_value=mock_proc):
            with self.assertRaisesRegex(RuntimeError, "gh pr diff failed: some error"):
                _gh("pr", "diff")

    def test_gh_success(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "some output"

        with patch("shutil.which", return_value="/usr/bin/gh"), \
             patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = _gh("pr", "diff")
            self.assertEqual(result, "some output")
            mock_run.assert_called_once_with(["gh", "pr", "diff"], capture_output=True, text=True)
