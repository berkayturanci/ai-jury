"""Unit tests for .pre-commit-hooks.yaml hook definition."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class PreCommitHookTests(unittest.TestCase):
    def test_pre_commit_hooks_file_exists_and_valid(self):
        hook_path = REPO_ROOT / ".pre-commit-hooks.yaml"
        self.assertTrue(hook_path.exists(), ".pre-commit-hooks.yaml must exist in root")

        content = hook_path.read_text(encoding="utf-8")
        self.assertIn("- id: ai-jury", content)
        # The hook pipes the staged diff into the review and must not pass filenames as
        # positional args (the review command has none, so that exits 2); see #831.
        self.assertIn("git diff --cached", content)
        self.assertIn("jury --diff-file -", content)
        self.assertIn("pass_filenames: false", content)
        self.assertIn("language: system", content)
        self.assertIn("stages:", content)


if __name__ == "__main__":
    unittest.main()
