"""Report-rendering regression tests (jury self-review findings)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.findings import Finding  # noqa: E402
from ai_jury.report import render  # noqa: E402


class RenderNoneSafetyTest(unittest.TestCase):
    def test_findings_with_none_file_do_not_crash_sort(self):
        # Two same-severity findings where one has file=None: the structured-
        # findings sort key must not compare None against a str (TypeError).
        findings = [
            Finding(severity="major", file=None, claim="unlocated issue", line=None),
            Finding(severity="major", file="src/a.py", claim="located issue", line=3),
        ]
        out = render([], [], None, chair="claude", findings=findings)
        self.assertIn("Structured findings", out)
        self.assertIn("located issue", out)
        self.assertIn("unlocated issue", out)


if __name__ == "__main__":
    unittest.main()
