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


class TldrHeadlineTest(unittest.TestCase):
    """The TL;DR callout hoists the verdict to the top of the report."""

    def _synth(self, text, ok=True):
        from ai_jury.adapters import AgentResult
        return AgentResult("codex", "openai", ok, text, 0.0)

    def test_lifts_verdict_line_from_synthesis(self):
        from ai_jury.report import _verdict_headline
        synth = self._synth("## Verdict\nREADY — the defect is clearly located.\n\n## Consensus gaps\n- none")
        self.assertEqual(_verdict_headline(synth, None),
                         "READY — the defect is clearly located.")

    def test_joins_a_wrapped_verdict_sentence(self):
        from ai_jury.report import _verdict_headline
        synth = self._synth("## Verdict\nNEEDS-INFO — the bug is clear\nbut lacks repro steps.\n\n## Gaps")
        self.assertEqual(_verdict_headline(synth, None),
                         "NEEDS-INFO — the bug is clear but lacks repro steps.")

    def test_header_directly_after_verdict_terminates_collection(self):
        from ai_jury.report import _verdict_headline
        synth = self._synth("## Verdict\nREADY — go.\n## Consensus gaps\n- none")
        self.assertEqual(_verdict_headline(synth, None), "READY — go.")

    def test_blank_lines_before_verdict_text_are_skipped(self):
        from ai_jury.report import _verdict_headline
        synth = self._synth("## Verdict\n\n\nNEEDS-INFO — missing repro.\n\n## Gaps")
        self.assertEqual(_verdict_headline(synth, None), "NEEDS-INFO — missing repro.")

    def test_vote_verdict_takes_precedence(self):
        from ai_jury.report import _verdict_headline
        from ai_jury.voting import VoteResult
        synth = self._synth("## Verdict\nAPPROVE — looks good.")
        self.assertEqual(_verdict_headline(synth, VoteResult("REQUEST CHANGES")),
                         "REQUEST CHANGES")

    def test_none_when_synthesis_failed_or_absent(self):
        from ai_jury.report import _verdict_headline
        self.assertIsNone(_verdict_headline(None, None))
        self.assertIsNone(_verdict_headline(self._synth("## Verdict\nX", ok=False), None))

    def test_none_when_no_verdict_section(self):
        from ai_jury.report import _verdict_headline
        self.assertIsNone(_verdict_headline(self._synth("## Summary\nno verdict here"), None))

    def test_callout_rendered_at_top_of_report(self):
        out = render([], [], self._synth("## Verdict\nAPPROVE — no blocking issues."),
                     chair="codex")
        self.assertIn("> ⚡ **TL;DR · APPROVE — no blocking issues.**", out)
        # The callout precedes the panel line.
        self.assertLess(out.index("TL;DR"), out.index("Structured findings"))
