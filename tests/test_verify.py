"""Tests for the verification round (issue #3)."""
import unittest

from agent_review_council.config import AgentSpec, CouncilConfig
from agent_review_council.findings import parse_verdicts
from agent_review_council.orchestrator import run_council


def _config(verify=True):
    return CouncilConfig(
        rounds=2,
        chair="claude",
        verify=verify,
        agents=[
            AgentSpec(name="claude", vendor="anthropic", command="claude"),
            AgentSpec(name="codex", vendor="openai", command="codex"),
        ],
    )


class ParseVerdictsTests(unittest.TestCase):
    def test_parse_valid(self):
        text = (
            '```json\n[{"file":"a.py","line":1,"claim":"x","status":"verified",'
            '"reasoning":"ok"}]\n```'
        )
        verdicts, warnings = parse_verdicts(text, "claude")
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(verdicts[0].status, "verified")
        self.assertEqual(verdicts[0].line, 1)
        self.assertEqual(warnings, [])

    def test_parse_garbage(self):
        verdicts, warnings = parse_verdicts("no json here", "claude")
        self.assertEqual(verdicts, [])
        self.assertTrue(warnings)

    def test_parse_empty(self):
        verdicts, warnings = parse_verdicts("", "claude")
        self.assertEqual(verdicts, [])
        self.assertTrue(warnings)

    def test_status_normalization(self):
        text = '```json\n[{"claim":"x","status":"NEEDS-HUMAN-DECISION"}]\n```'
        verdicts, _ = parse_verdicts(text)
        self.assertEqual(verdicts[0].status, "needs_human_decision")

    def test_unknown_status_falls_back(self):
        text = '```json\n[{"claim":"x","status":"maybe"}]\n```'
        verdicts, _ = parse_verdicts(text)
        self.assertEqual(verdicts[0].status, "needs_human_decision")

    def test_malformed_json_warns(self):
        verdicts, warnings = parse_verdicts("```json\n[not json]\n```", "claude")
        self.assertEqual(verdicts, [])
        self.assertTrue(warnings)


class VerifyRoundTests(unittest.TestCase):
    def test_verify_runs_and_attaches_status(self):
        outcome = run_council(_config(verify=True), "fake diff", mock=True)
        self.assertIsNotNone(outcome.verify)
        self.assertTrue(outcome.verify.ok)
        self.assertTrue(outcome.verdicts)
        statuses = {v.status for v in outcome.verdicts}
        self.assertIn("verified", statuses)
        self.assertIn("unsupported", statuses)

        by_line = {g.representative.line: g for g in outcome.groups}
        # major @ :42 verified; minor @ :7 unsupported -> rejected bucket.
        self.assertEqual(by_line[42].status, "verified")
        self.assertEqual(by_line[7].status, "unsupported")
        self.assertEqual(by_line[7].bucket, "rejected")

    def test_verify_disabled_skips_phase(self):
        outcome = run_council(_config(verify=False), "fake diff", mock=True)
        self.assertIsNone(outcome.verify)
        self.assertEqual(outcome.verdicts, [])
        for g in outcome.groups:
            self.assertEqual(g.status, "")


if __name__ == "__main__":
    unittest.main()
