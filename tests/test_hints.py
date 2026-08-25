"""Unit tests for static analysis hints pre-pass (issue #523)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.hints import collect_static_hints  # noqa: E402


class HintsTests(unittest.TestCase):
    def test_collect_static_hints_empty_when_no_linters(self):
        with patch("shutil.which", return_value=None):
            hints = collect_static_hints(files=["src/ai_jury/config.py"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")

    def test_collect_static_hints_with_ruff_output(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "src/main.py:10:1: F401 `os` imported but unused\nsrc/main.py:12:1: E501 line too long\n"

        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "ruff" else None,
            ),
            patch("subprocess.run", return_value=mock_proc),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
            self.assertIn("Python linter (Ruff) warnings:", hints)
            self.assertIn("F401", hints)
            self.assertIn("Static Analysis Hints", hints)

    def test_collect_static_hints_with_ruff_no_files_and_clean(self):
        mock_clean = MagicMock(returncode=0, stdout="")
        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "ruff" else None,
            ),
            patch("subprocess.run", return_value=mock_clean),
        ):
            hints = collect_static_hints(files=None, root_dir=REPO_ROOT)
            self.assertEqual(hints, "")

    def test_collect_static_hints_with_eslint_output(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = "src/app.ts: line 5, col 10, Error - Unexpected any (no-explicit-any)\n"

        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "npx" else None,
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=mock_proc),
        ):
            hints = collect_static_hints(files=["src/app.ts"], root_dir=REPO_ROOT)
            self.assertIn("JS/TS linter (ESLint) warnings:", hints)
            self.assertIn("no-explicit-any", hints)

    def test_collect_static_hints_with_eslint_clean_or_no_js(self):
        mock_clean = MagicMock(returncode=0, stdout="")
        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "npx" else None,
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=mock_clean),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")

    def test_collect_static_hints_with_eslint_no_files(self):
        mock_proc = MagicMock(returncode=1, stdout="app.js:1:1: error\n")
        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "npx" else None,
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", return_value=mock_proc),
        ):
            hints = collect_static_hints(files=None, root_dir=REPO_ROOT)
            self.assertIn("ESLint", hints)

    def test_collect_static_hints_exception_tolerance(self):
        with (
            patch("shutil.which", return_value="/usr/bin/ruff"),
            patch("subprocess.run", side_effect=RuntimeError("boom")),
        ):
            hints = collect_static_hints(files=["src/main.py"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")

        with (
            patch(
                "shutil.which",
                side_effect=lambda name: "/usr/bin/" + name if name == "npx" else None,
            ),
            patch("pathlib.Path.exists", return_value=True),
            patch("subprocess.run", side_effect=RuntimeError("boom")),
        ):
            hints = collect_static_hints(files=["src/app.js"], root_dir=REPO_ROOT)
            self.assertEqual(hints, "")


if __name__ == "__main__":
    unittest.main()
