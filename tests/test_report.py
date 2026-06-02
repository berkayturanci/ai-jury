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


class RenderSectionsTest(unittest.TestCase):
    def test_three_sections_with_debate(self):
        from ai_jury.adapters import AgentResult
        from ai_jury.report import render_sections

        reviews = [AgentResult("claude", "anthropic", True, "r-claude", 0.0),
                   AgentResult("codex", "openai", True, "r-codex", 0.0)]
        debate = [AgentResult("claude", "anthropic", True, "d-claude", 0.0)]
        synth = AgentResult("claude", "anthropic", True, "## Verdict\nAPPROVE", 0.0)
        secs = render_sections(reviews, debate, synth, chair="claude",
                               findings=[], groups=[])
        titles = [t for t, _ in secs]
        self.assertEqual(len(secs), 3)
        self.assertIn("Round 1", titles[0])
        self.assertIn("Round 2", titles[1])
        self.assertIn("Decision", titles[2])
        self.assertIn("r-claude", secs[0][1])
        self.assertIn("d-claude", secs[1][1])
        self.assertIn("APPROVE", secs[2][1])

    def test_debate_section_omitted_when_no_debate(self):
        from ai_jury.adapters import AgentResult
        from ai_jury.report import render_sections

        reviews = [AgentResult("claude", "anthropic", True, "r", 0.0)]
        secs = render_sections(reviews, [], None, chair="claude")
        self.assertEqual(len(secs), 2)  # Round 1 + Decision, no debate
        self.assertNotIn("Round 2", " ".join(t for t, _ in secs))


class EvidenceSurfacingTest(unittest.TestCase):
    def test_consensus_line_shows_evidence(self):
        from ai_jury.consensus import group_findings
        from ai_jury.findings import Finding
        from ai_jury.report import render

        f = Finding(severity="major", file="src/a.py", line=42, claim="bug",
                    evidence="int(x) return value ignored", reviewer="claude")
        groups = group_findings([f], reviewer_count=1)
        out = render([], [], None, chair="claude", findings=[f], groups=groups)
        self.assertIn("_evidence:_ int(x) return value ignored", out)

    def test_no_evidence_line_when_absent(self):
        from ai_jury.consensus import group_findings
        from ai_jury.findings import Finding
        from ai_jury.report import render

        f = Finding(severity="major", file="src/a.py", line=42, claim="bug", reviewer="claude")
        groups = group_findings([f], reviewer_count=1)
        out = render([], [], None, chair="claude", findings=[f], groups=groups)
        self.assertNotIn("_evidence:_", out)
