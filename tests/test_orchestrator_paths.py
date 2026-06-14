"""Coverage for orchestrator adaptive-rounds / budget / chunk branches.
Deterministic via mock agents + a seed."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.config import DEFAULT_CONFIG, _from_dict  # noqa: E402
from ai_jury.orchestrator import RunBudget, review_diff, run_jury  # noqa: E402

DIFF = """diff --git a/a.py b/a.py
index 0000000..1111111 100644
--- a/a.py
+++ b/a.py
@@ -1 +1,2 @@
-x = 1
+x = 2
+y = 3
"""


def _cfg(**over):
    c = _from_dict(DEFAULT_CONFIG)
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _single_agent_cfg(**over):
    data = dict(DEFAULT_CONFIG)
    data = {**data, "agent": [{"name": "claude", "vendor": "anthropic", "command": "claude"}]}
    c = _from_dict(data)
    c.chair = "claude"
    for k, v in over.items():
        setattr(c, k, v)
    return c


class FixedNPaths(unittest.TestCase):
    def test_two_rounds_budget_expired_skips_debate(self):
        out = run_jury(
            _cfg(rounds=2, early_stop=False), DIFF, mock=True, seed=1, budget=RunBudget(0, None)
        )
        self.assertEqual(out.debate, [])
        self.assertTrue(out.budget_exhausted)

    def test_single_agent_cannot_debate(self):
        out = run_jury(_single_agent_cfg(rounds=2, early_stop=False), DIFF, mock=True, seed=1)
        self.assertEqual(out.debate, [])


class EarlyStopPaths(unittest.TestCase):
    def test_single_agent_stops_after_round_one(self):
        out = run_jury(_single_agent_cfg(early_stop=True, max_rounds=3), DIFF, mock=True, seed=1)
        self.assertEqual(out.debate, [])

    def test_max_rounds_below_two(self):
        out = run_jury(_cfg(early_stop=True, max_rounds=1), DIFF, mock=True, seed=1)
        self.assertEqual(out.debate, [])

    def test_early_stop_full_panel_runs(self):
        # Exercises the convergence decision + whichever branch it takes.
        out = run_jury(_cfg(early_stop=True, max_rounds=3), DIFF, mock=True, seed=1)
        self.assertIsNotNone(out.synthesis)
        self.assertGreaterEqual(out.rounds_executed, 1)

    def test_early_stop_budget_expired(self):
        # Round 1 runs; an already-expired budget then skips debate (and may skip
        # later phases) — the run still returns an outcome (partial-result policy).
        out = run_jury(
            _cfg(early_stop=True, max_rounds=3), DIFF, mock=True, seed=1, budget=RunBudget(0, None)
        )
        self.assertTrue(out.reviews)
        self.assertEqual(out.debate, [])


class ChunkAndScopePaths(unittest.TestCase):
    def test_review_diff_chunked(self):
        # Multi-file diff over a tiny budget with chunking on → chunk + merge.
        big = DIFF + DIFF.replace("a.py", "b.py") + DIFF.replace("a.py", "c.py")
        cfg = _cfg(rounds=1)
        cfg.diff.chunk = True
        cfg.diff.max_bytes = 80  # force over-budget so it chunks by file
        outcome, plan = review_diff(cfg, big, mock=True)
        self.assertIsNotNone(outcome)

    def test_review_diff_full(self):
        outcome, plan = review_diff(_cfg(rounds=1), DIFF, mock=True)
        self.assertTrue(outcome.reviews)


if __name__ == "__main__":
    unittest.main()
