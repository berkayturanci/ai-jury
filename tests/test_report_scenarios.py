"""Coverage for report.render branch shapes not in the golden fixtures:
cache-hit + budget-exhausted metadata, review-scope note, failed verification,
the structured-findings fallback (no consensus groups), and agent warnings."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.findings import Finding  # noqa: E402
from ai_jury.report import render, render_sections  # noqa: E402


def _ar(ok=True, output="text", error=None):
    return AgentResult("claude", "anthropic", ok, output, 1.0, error=error)


class ReportScenarioTests(unittest.TestCase):
    def test_cache_budget_scope_verifyfail_findings_warnings(self):
        report = render(
            [_ar(output="round 1")],
            [],
            _ar(output="## Verdict\nAPPROVE"),
            chair="claude",
            findings=[Finding(severity="major", file="a.py", claim="boom", line=3)],
            warnings=["qwen: malformed output"],
            groups=[],  # no consensus groups → structured-findings fallback
            verify=_ar(ok=False, output="", error="verify crashed"),
            metadata={
                "rounds_executed": 2,
                "verify_enabled": True,
                "context_mode": "diff-only",
                "total_wall_clock_s": 3.0,
                "agents": [
                    {"name": "claude", "vendor": "anthropic", "status": "ok", "duration_s": 1.0}
                ],
                "from_cache": True,
                "budget_exhausted": True,
            },
            review_scope="_Reviewing changes since the last run._",
        )
        self.assertIn("served from local cache", report)
        self.assertIn("budget exhausted", report)
        self.assertIn("Reviewing changes since the last run", report)
        self.assertIn("Verification failed", report)
        self.assertIn("Structured findings", report)
        self.assertIn("agent output warnings", report)
        self.assertIn("qwen: malformed output", report)

    def test_render_sections_phased_decision(self):
        sections = render_sections(
            [_ar(output="round 1")],
            [_ar(output="debate exchange")],  # debate present → Round 2 section
            _ar(output="## Verdict\nCOMMENT"),
            chair="claude",
            findings=[Finding(severity="major", file="a.py", claim="boom", line=3)],
            warnings=["qwen: malformed output"],
            groups=[],  # structured-findings fallback inside Decision
            verify=_ar(ok=False, output="", error="verify crashed"),
        )
        titles = [t for t, _ in sections]
        self.assertTrue(any("Round 1" in t for t in titles))
        self.assertTrue(any("Round 2" in t for t in titles))
        self.assertTrue(any("Decision" in t for t in titles))
        blob = "\n".join(b for _, b in sections)
        self.assertIn("Structured findings", blob)
        self.assertIn("Verification failed", blob)
        self.assertIn("agent output warnings", blob)

    def test_failed_synthesis_branch(self):
        report = render(
            [_ar(output="round 1")],
            [],
            _ar(ok=False, output="", error="synthesis crashed"),
            chair="claude",
        )
        self.assertIn("Synthesis failed", report)


if __name__ == "__main__":
    unittest.main()
