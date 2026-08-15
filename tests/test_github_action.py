"""Unit tests for action.yml GitHub Action definition."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class GitHubActionTests(unittest.TestCase):
    def test_action_file_exists_and_valid(self):
        action_path = REPO_ROOT / "action.yml"
        self.assertTrue(action_path.exists(), "action.yml must exist in root")

        content = action_path.read_text(encoding="utf-8")
        self.assertIn('name: "ai-jury Review"', content)
        self.assertIn('using: "composite"', content)
        self.assertIn("github-token:", content)
        self.assertIn("openai-api-key:", content)
        self.assertIn("anthropic-api-key:", content)
        self.assertIn("gemini-api-key:", content)
        self.assertIn("jury --pr", content)


if __name__ == "__main__":
    unittest.main()
