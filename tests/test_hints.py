"""Unit tests for static analysis hints pre-pass (issue #523)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.hints import collect_static_hints  # noqa: E402


class HintsTests(unittest.TestCase):
    def test_collect_static_hints_does_not_throw(self):
        # Even with no modified files or random paths, collect_static_hints must be safe
        hints = collect_static_hints(files=["src/ai_jury/config.py"], root_dir=REPO_ROOT)
        self.assertIsInstance(hints, str)


if __name__ == "__main__":
    unittest.main()
