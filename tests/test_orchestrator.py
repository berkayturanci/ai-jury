"""Offline tests for the council pipeline using mock adapters.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live CLIs, no network.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council.config import DEFAULT_CONFIG, CouncilConfig, _from_dict  # noqa: E402
from agent_review_council.orchestrator import run_council  # noqa: E402
from agent_review_council.report import render  # noqa: E402

SAMPLE_DIFF = """diff --git a/src/example.py b/src/example.py
@@ -1,3 +1,6 @@
+def parse(x):
+    return int(x)
"""


def _config() -> CouncilConfig:
    return _from_dict(DEFAULT_CONFIG)


class CouncilPipelineTest(unittest.TestCase):
    def test_full_pipeline_runs_in_mock_mode(self):
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True)
        self.assertEqual(len(outcome.reviews), 3)
        self.assertTrue(all(r.ok for r in outcome.reviews))
        self.assertEqual(len(outcome.debate), 3)
        self.assertIsNotNone(outcome.synthesis)
        self.assertTrue(outcome.synthesis.ok)

    def test_chair_is_configured_agent(self):
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True)
        self.assertEqual(outcome.chair, "claude")

    def test_single_round_skips_debate(self):
        cfg = _config()
        cfg.rounds = 1
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        self.assertEqual(outcome.debate, [])
        self.assertIsNotNone(outcome.synthesis)

    def test_chair_falls_back_when_unavailable(self):
        cfg = _config()
        cfg.chair = "nonexistent"
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        self.assertEqual(outcome.chair, "claude")

    def test_report_contains_verdict_and_panel(self):
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True)
        report = render(outcome.reviews, outcome.debate, outcome.synthesis, chair=outcome.chair)
        self.assertIn("Agent Review Council", report)
        self.assertIn("Chair verdict", report)
        self.assertIn("Round 1", report)
        self.assertIn("Round 2", report)
        self.assertIn("claude", report)
        self.assertIn("codex", report)
        self.assertIn("agy", report)

    def test_no_usable_agents_raises(self):
        cfg = _from_dict({"council": {}, "agent": []})
        with self.assertRaises(RuntimeError):
            run_council(cfg, SAMPLE_DIFF, mock=True)

    def test_mock_pipeline_produces_structured_findings(self):
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True)
        # Aggregated on the outcome: 2 findings per reviewer x 3 reviewers.
        self.assertGreaterEqual(len(outcome.findings), 1)
        self.assertEqual(len(outcome.findings), 6)
        # No finding-parser warnings on clean mock output. (Least-privilege
        # advisories about the default codex/agy config may be present and are
        # asserted separately in LeastPrivilegeAuditTest.)
        parser_warnings = [
            w for w in outcome.warnings
            if "structured findings" in w or "verdicts" in w
        ]
        self.assertEqual(parser_warnings, [])
        # Findings attached to each review, reviewer preserved.
        for r in outcome.reviews:
            self.assertTrue(r.findings)
            for f in r.findings:
                self.assertEqual(f.reviewer, r.agent)
        severities = {f.severity for f in outcome.findings}
        self.assertIn("major", severities)

    def test_report_contains_structured_findings_section(self):
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True)
        report = render(
            outcome.reviews,
            outcome.debate,
            outcome.synthesis,
            chair=outcome.chair,
            findings=outcome.findings,
            warnings=outcome.warnings,
        )
        self.assertIn("Structured findings", report)
        self.assertIn("[major]", report)
        self.assertIn("src/example.py:42", report)


if __name__ == "__main__":
    unittest.main()
