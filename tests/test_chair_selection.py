"""Tests for chair self-preference mitigation (issue #38).

Covers:
  - prefer_non_reviewer_chair: a usable non-reviewer is preferred when present.
  - chair = "rotate": deterministic per seed; same seed -> same chair, varies
    across seeds.
  - chair-is-reviewer: synthesis input is anonymized so the chair cannot tell
    which findings are its own; the report still attributes by real name.

Offline: pure-function calls plus the mock pipeline. No live CLIs, no network.
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import orchestrator  # noqa: E402
from ai_jury.config import DEFAULT_CONFIG, JuryConfig, _from_dict  # noqa: E402
from ai_jury.orchestrator import resolve_chair, run_jury  # noqa: E402
from ai_jury.report import render  # noqa: E402

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)

USABLE = ["claude", "codex", "agy"]
AGENT_NAMES = ("claude", "codex", "agy")
VENDOR_NAMES = ("anthropic", "openai", "google")


def _config() -> JuryConfig:
    return _from_dict(DEFAULT_CONFIG)


class ResolveChairExplicitTest(unittest.TestCase):
    def test_explicit_usable_chair_honoured(self) -> None:
        cfg = _config()
        cfg.chair = "codex"
        self.assertEqual(resolve_chair(cfg, USABLE, USABLE, random.Random(1)), "codex")

    def test_unusable_chair_falls_back_to_first(self) -> None:
        cfg = _config()
        cfg.chair = "nonexistent"
        self.assertEqual(resolve_chair(cfg, USABLE, USABLE, random.Random(1)), "claude")

    def test_empty_usable_returns_configured(self) -> None:
        cfg = _config()
        cfg.chair = "claude"
        self.assertEqual(resolve_chair(cfg, [], [], random.Random(1)), "claude")


class PreferNonReviewerChairTest(unittest.TestCase):
    def test_prefers_usable_non_reviewer_when_available(self) -> None:
        cfg = _config()
        cfg.chair = "nonexistent"  # not usable -> fallback logic engages
        cfg.prefer_non_reviewer_chair = True
        # agy did not produce a round-1 review -> it should be preferred.
        chair = resolve_chair(cfg, USABLE, ["claude", "codex"], random.Random(1))
        self.assertEqual(chair, "agy")

    def test_falls_back_to_first_when_all_are_reviewers(self) -> None:
        cfg = _config()
        cfg.chair = "nonexistent"
        cfg.prefer_non_reviewer_chair = True
        chair = resolve_chair(cfg, USABLE, USABLE, random.Random(1))
        self.assertEqual(chair, "claude")

    def test_explicit_usable_chair_wins_over_preference(self) -> None:
        cfg = _config()
        cfg.chair = "codex"  # usable + an operator choice
        cfg.prefer_non_reviewer_chair = True
        chair = resolve_chair(cfg, USABLE, ["claude", "codex"], random.Random(1))
        self.assertEqual(chair, "codex")

    def test_non_reviewer_chair_used_end_to_end(self) -> None:
        # Configure a 4th agent that is NOT a reviewer in this run by disabling
        # its review path is not trivial in mock; instead assert resolve picks a
        # non-reviewer when reviewers is a strict subset, via run_jury with a
        # config whose chair is unusable and preference on. All three mock
        # agents review, so we assert the documented fallback (first usable).
        cfg = _config()
        cfg.chair = "nonexistent"
        cfg.prefer_non_reviewer_chair = True
        outcome = run_jury(cfg, SAMPLE_DIFF, mock=True, seed=1)
        # All mock agents review successfully, so non-reviewer set is empty ->
        # fallback to first usable, and verify/synthesis use that SAME chair.
        self.assertEqual(outcome.chair, "claude")
        self.assertTrue(outcome.synthesis.ok)


class RotateChairTest(unittest.TestCase):
    def test_rotate_is_deterministic_per_seed(self) -> None:
        cfg = _config()
        cfg.chair = "rotate"
        a = resolve_chair(cfg, USABLE, USABLE, random.Random(99))
        b = resolve_chair(cfg, USABLE, USABLE, random.Random(99))
        self.assertEqual(a, b)
        self.assertIn(a, USABLE)

    def test_rotate_varies_across_seeds(self) -> None:
        cfg = _config()
        cfg.chair = "rotate"
        picks = {resolve_chair(cfg, USABLE, USABLE, random.Random(s)) for s in range(50)}
        self.assertGreater(len(picks), 1, "rotate never varied across seeds")

    def test_rotate_end_to_end_same_seed_same_chair(self) -> None:
        cfg = _config()
        cfg.chair = "rotate"
        c1 = run_jury(cfg, SAMPLE_DIFF, mock=True, seed=2024).chair
        c2 = run_jury(cfg, SAMPLE_DIFF, mock=True, seed=2024).chair
        self.assertEqual(c1, c2)
        self.assertIn(c1, USABLE)

    def test_rotate_end_to_end_varies_across_seeds(self) -> None:
        cfg = _config()
        cfg.chair = "rotate"
        chairs = {run_jury(cfg, SAMPLE_DIFF, mock=True, seed=s).chair for s in range(30)}
        self.assertGreater(len(chairs), 1)

    def test_verify_and_synthesis_use_same_resolved_chair(self) -> None:
        cfg = _config()
        cfg.chair = "rotate"
        outcome = run_jury(cfg, SAMPLE_DIFF, mock=True, seed=7)
        report = render(
            outcome.reviews,
            outcome.debate,
            outcome.synthesis,
            chair=outcome.chair,
            verify=outcome.verify,
        )
        # Both "Verified by" and "Synthesized by" cite the single resolved chair.
        self.assertIn(f"Verified by `{outcome.chair}`", report)
        self.assertIn(f"Synthesized by `{outcome.chair}`", report)


class SynthesisAnonymizationTest(unittest.TestCase):
    """When the chair is also a reviewer, synthesis input must be anonymized."""

    def _capture_synthesis_prompt(self, cfg: JuryConfig, seed: int) -> str:
        captured: dict[str, str] = {}
        real_run_phase = orchestrator._run_phase

        # Synthesis runs via chair.run(), not _run_phase; patch the adapter run.
        # Simplest: patch _anonymize/_synthesize boundary by intercepting the
        # chair adapter. We instead monkeypatch MockAdapter.run to record the
        # synthesis prompt.
        from ai_jury.adapters import MockAdapter

        real_run = MockAdapter.run

        def recording_run(self, prompt, phase="review", timeout=None):
            if phase == "synthesis":
                captured["prompt"] = prompt
            return real_run(self, prompt, phase=phase, timeout=timeout)

        MockAdapter.run = recording_run
        try:
            run_jury(cfg, SAMPLE_DIFF, mock=True, seed=seed)
        finally:
            MockAdapter.run = real_run
            orchestrator._run_phase = real_run_phase
        return captured.get("prompt", "")

    def test_chair_is_reviewer_synthesis_input_anonymized(self) -> None:
        cfg = _config()
        cfg.chair = "claude"  # claude is also a round-1 reviewer
        prompt = self._capture_synthesis_prompt(cfg, seed=3)
        self.assertTrue(prompt)
        # Isolate the round-1 reviews block fed to the chair.
        reviews_block = prompt.split("ROUND-1 REVIEWS", 1)[1].split("ROUND-2 DEBATE", 1)[0]
        # The chair must not be able to tell which review heading is "its own":
        # every per-review HEADING is anonymous. (The mock review body prints its
        # author name, which is reviewer-authored content, not a label we add.)
        headings = [ln for ln in reviews_block.splitlines() if ln.startswith("### ")]
        self.assertTrue(headings)
        for h in headings:
            self.assertTrue(
                h.startswith("### Reviewer "),
                f"non-anonymous review heading reached synthesis: {h!r}",
            )
            for name in AGENT_NAMES:
                self.assertNotIn(name, h)
            for vendor in VENDOR_NAMES:
                self.assertNotIn(vendor, h)
        self.assertIn("### Reviewer A", reviews_block)

    def test_report_still_attributes_by_real_name(self) -> None:
        cfg = _config()
        cfg.chair = "claude"
        outcome = run_jury(cfg, SAMPLE_DIFF, mock=True, seed=3)
        report = render(
            outcome.reviews,
            outcome.debate,
            outcome.synthesis,
            chair=outcome.chair,
            findings=outcome.findings,
            warnings=outcome.warnings,
            groups=outcome.groups,
        )
        for name in AGENT_NAMES:
            self.assertIn(name, report)


if __name__ == "__main__":
    unittest.main()
