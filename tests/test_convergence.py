"""Tests for convergence-based early stop / adaptive debate rounds (issue #40).

Offline: pure-function fixtures plus a mock pipeline with custom adapters; no
live CLIs, no network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.adapters import AgentResult, MockAdapter  # noqa: E402
from ai_jury.config import DEFAULT_CONFIG, _from_dict  # noqa: E402
from ai_jury.consensus import group_findings  # noqa: E402
from ai_jury.convergence import (  # noqa: E402
    debate_convergence,
    review_convergence,
)
from ai_jury.findings import Finding  # noqa: E402
from ai_jury.metadata import build_run_metadata  # noqa: E402
from ai_jury.orchestrator import run_jury  # noqa: E402

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)


def _config():
    return _from_dict(DEFAULT_CONFIG)


def _finding(reviewer, file="src/a.py", line=10, claim="bug here"):
    return Finding(severity="major", file=file, claim=claim, line=line, reviewer=reviewer)


class ReviewConvergenceTest(unittest.TestCase):
    def test_no_findings_converges(self):
        converged, reason = review_convergence([], reviewer_count=3)
        self.assertTrue(converged)
        self.assertIn("no findings", reason)

    def test_single_reviewer_converges(self):
        groups = group_findings([_finding("a")], reviewer_count=1)
        converged, _ = review_convergence(groups, reviewer_count=1)
        self.assertTrue(converged)

    def test_unanimous_findings_converge(self):
        findings = [_finding(r) for r in ("a", "b", "c")]
        groups = group_findings(findings, reviewer_count=3)
        converged, reason = review_convergence(groups, reviewer_count=3)
        self.assertTrue(converged)
        self.assertIn("unanimous", reason)

    def test_disagreement_does_not_converge(self):
        # Three reviewers each flag a *different* file -> three single-reviewer
        # groups -> not unanimous.
        findings = [
            _finding("a", file="src/a.py"),
            _finding("b", file="src/b.py"),
            _finding("c", file="src/c.py"),
        ]
        groups = group_findings(findings, reviewer_count=3)
        converged, reason = review_convergence(groups, reviewer_count=3)
        self.assertFalse(converged)
        self.assertIn("non-unanimous", reason)


class DebateConvergenceTest(unittest.TestCase):
    def _result(self, output):
        return AgentResult("x", "v", True, output, 0.0)

    def test_disputes_present_not_converged(self):
        out = "## AGREE\n- yes\n## DISPUTE\n- I disagree with finding 2\n"
        converged, _ = debate_convergence([self._result(out)])
        self.assertFalse(converged)

    def test_no_disputes_converged(self):
        out = "## AGREE\n- all good\n## DISPUTE\n- none\n## MISSED\n- none\n"
        converged, reason = debate_convergence([self._result(out)])
        self.assertTrue(converged)
        self.assertIn("no unresolved", reason)

    def test_missed_counts_as_disagreement(self):
        out = "## AGREE\n- ok\n## MISSED\n- there is an untested branch\n"
        converged, _ = debate_convergence([self._result(out)])
        self.assertFalse(converged)


class _DivergentAdapter(MockAdapter):
    """Each agent flags a different file so round-1 reviews never converge."""

    def run(self, prompt, phase="review", timeout=None):
        if phase == "review":
            n = self.name
            body = (
                "```json\n"
                "[\n"
                f'  {{"severity": "major", "file": "src/{n}.py", "line": 3, '
                f'"claim": "{n}-only issue in its own file", "reviewer": "{n}"}}\n'
                "]\n"
                "```"
            )
            return AgentResult(n, self.spec.vendor, True, body, 0.0)
        return super().run(prompt, phase=phase, timeout=timeout)


class _UnanimousAdapter(MockAdapter):
    """Every agent raises the *same* single finding (identical claim, same file
    and line) so the panel is fully unanimous after round 1."""

    def run(self, prompt, phase="review", timeout=None):
        if phase == "review":
            body = (
                "```json\n"
                "[\n"
                '  {"severity": "major", "file": "src/example.py", "line": 42, '
                '"claim": "unchecked return value may swallow an error", '
                f'"reviewer": "{self.name}"}}\n'
                "]\n"
                "```"
            )
            return AgentResult(self.name, self.spec.vendor, True, body, 0.0)
        return super().run(prompt, phase=phase, timeout=timeout)


class EarlyStopPipelineTest(unittest.TestCase):
    def test_unanimous_run_skips_debate(self):
        # When every reviewer raises the identical finding the panel is unanimous,
        # so early-stop skips the debate round entirely.
        import ai_jury.orchestrator as orch

        cfg = _config()
        cfg.early_stop = True
        cfg.max_rounds = 3
        real_make = orch.make_adapter
        orch.make_adapter = lambda spec, mock=False: _UnanimousAdapter(spec)  # noqa: ARG005
        try:
            outcome = run_jury(cfg, SAMPLE_DIFF, mock=True)
        finally:
            orch.make_adapter = real_make
        self.assertEqual(outcome.debate, [])
        self.assertEqual(outcome.rounds_executed, 1)
        self.assertIn("early stop after round 1", outcome.stop_reason)

    def test_disagreement_runs_debate_up_to_max_rounds(self):
        import ai_jury.orchestrator as orch

        cfg = _config()
        cfg.early_stop = True
        cfg.max_rounds = 3
        real_make = orch.make_adapter
        orch.make_adapter = lambda spec, mock=False: _DivergentAdapter(spec)  # noqa: ARG005
        try:
            outcome = run_jury(cfg, SAMPLE_DIFF, mock=True)
        finally:
            orch.make_adapter = real_make
        # Disagreement -> debate runs; the mock debate always disputes, so it runs
        # all the way to the max_rounds ceiling.
        self.assertTrue(outcome.debate)
        self.assertEqual(outcome.rounds_executed, 3)
        meta = build_run_metadata(outcome, cfg)
        self.assertEqual(meta["rounds_executed"], 3)
        self.assertTrue(meta["stop_reason"])

    def test_fixed_rounds_unaffected_by_early_stop_default(self):
        # Default config has early_stop = False: rounds=2 always debates.
        cfg = _config()
        self.assertFalse(cfg.early_stop)
        outcome = run_jury(cfg, SAMPLE_DIFF, mock=True)
        self.assertEqual(outcome.rounds_executed, 2)
        self.assertTrue(outcome.debate)


if __name__ == "__main__":
    unittest.main()
