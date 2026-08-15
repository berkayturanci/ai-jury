"""Unit tests for Formula/ai-jury.rb and install.sh installer script."""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury import __version__  # noqa: E402


class HomebrewFormulaTests(unittest.TestCase):
    def test_formula_file_exists_and_matches_version(self):
        formula_path = REPO_ROOT / "Formula" / "ai-jury.rb"
        self.assertTrue(formula_path.exists(), "Formula/ai-jury.rb must exist")

        content = formula_path.read_text(encoding="utf-8")
        self.assertIn("class AiJury < Formula", content)
        self.assertIn("include Language::Python::Virtualenv", content)
        self.assertIn(f"ai_jury-{__version__}.tar.gz", content)

        # Validate sha256 pattern (64 hex characters)
        sha_match = re.search(r'sha256\s+"([0-9a-f]{64})"', content)
        self.assertIsNotNone(sha_match, "Formula must contain a valid 64-char sha256 checksum")

        # Validate test block
        self.assertIn("assert_match", content)
        self.assertIn(f"jury {__version__}", content)

    def test_install_script_syntax_and_structure(self):
        install_script = REPO_ROOT / "install.sh"
        self.assertTrue(install_script.exists(), "install.sh must exist")

        # Verify shell syntax via sh -n
        res = subprocess.run(["sh", "-n", str(install_script)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"install.sh has syntax error: {res.stderr}")

        content = install_script.read_text(encoding="utf-8")
        self.assertIn("berkayturanci/ai-jury/ai-jury", content)
        self.assertIn("pipx install ai-jury", content)


if __name__ == "__main__":
    unittest.main()
