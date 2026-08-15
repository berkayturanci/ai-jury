"""Unit tests for viral growth features, cost economics estimation, and Keel flywheel."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ai_jury.adapters import AgentResult  # noqa: E402
from ai_jury.metadata import estimate_economics  # noqa: E402
from ai_jury.report import render  # noqa: E402


class GrowthAndEconomicsTests(unittest.TestCase):
    def test_estimate_economics_with_local_and_cloud_models(self):
        r1 = AgentResult(agent="claude", vendor="anthropic", output="Found a minor bug in auth.py.", duration_s=2.5, ok=True)
        r2 = AgentResult(agent="deepseek", vendor="deepseek", output="Looks clean to me.", duration_s=1.8, ok=True)
        r3 = AgentResult(agent="local-qwen", vendor="local", output="No issues found.", duration_s=3.0, ok=True)

        econ = estimate_economics([r1, r2, r3])
        self.assertGreater(econ["total_tokens_est"], 1000)
        self.assertGreater(econ["total_cost_usd_est"], 0.0)
        self.assertEqual(econ["local_free_slots"], 1)

        breakdown = {b["agent"]: b for b in econ["breakdown"]}
        self.assertEqual(breakdown["local-qwen"]["cost_usd_est"], 0.0)
        self.assertTrue(breakdown["local-qwen"]["is_local_free"])
        self.assertGreater(breakdown["claude"]["cost_usd_est"], 0.0)

    def test_report_renders_economics_and_viral_footer(self):
        r1 = AgentResult(agent="claude", vendor="anthropic", output="Everything looks good.", duration_s=2.0, ok=True)
        r2 = AgentResult(agent="local-qwen", vendor="local", output="Agree.", duration_s=2.0, ok=True)
        econ = estimate_economics([r1, r2])

        metadata = {
            "rounds_executed": 1,
            "verify_enabled": False,
            "context_mode": "diff-only",
            "total_wall_clock_s": 4.0,
            "agents": [
                {"name": "claude", "vendor": "anthropic", "status": "ok", "duration_s": 2.0},
                {"name": "local-qwen", "vendor": "local", "status": "ok", "duration_s": 2.0},
            ],
            "economics": econ,
        }

        rendered = render([r1, r2], [], None, chair="claude", metadata=metadata)
        self.assertIn("### 💰 Run Economics (estimated)", rendered)
        self.assertIn("slot(s) powered by local models ($0.00 free offline)", rendered)
        self.assertIn("🏛️ Synthesized by [ai-jury]", rendered)
        self.assertIn("[⭐ Star on GitHub]", rendered)
        self.assertIn("[Add to your repo]", rendered)

    def test_cookbook_documents_keel_and_badges(self):
        cookbook_path = REPO_ROOT / "docs" / "cookbook.md"
        content = cookbook_path.read_text(encoding="utf-8")
        self.assertIn("Full delivery governance with Keel + AI Jury", content)
        self.assertIn("https://github.com/berkayturanci/keel", content)
        self.assertIn("ai--jury-consensus%20verified-6366f1", content)


if __name__ == "__main__":
    unittest.main()
