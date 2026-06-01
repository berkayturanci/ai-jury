"""Tests for anonymized peer feedback in the debate round (issue #37).

Chatham House rule: in round 2 each debater sees the OTHER reviewers' round-1
findings stripped of vendor/agent identity ("Reviewer A", "Reviewer B", ...) and
in a per-debater, seed-deterministic order, so neither identity nor position is a
stable bias signal. The debater's own review is excluded from the peer set. The
rendered report still attributes everything by real name.

Offline: uses the mock pipeline plus direct helper calls. No live CLIs, no
network.
"""
from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council import orchestrator, prompts  # noqa: E402
from agent_review_council.adapters import AgentResult  # noqa: E402
from agent_review_council.config import DEFAULT_CONFIG, CouncilConfig, _from_dict  # noqa: E402
from agent_review_council.orchestrator import (  # noqa: E402
    _anon_label,
    _anonymize_peers,
    run_council,
)
from agent_review_council.report import render  # noqa: E402

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)

# All agent names and vendors that must NEVER leak into an anonymized peer block.
AGENT_NAMES = ("claude", "codex", "agy")
VENDOR_NAMES = ("anthropic", "openai", "google")


def _config() -> CouncilConfig:
    return _from_dict(DEFAULT_CONFIG)


def _fake_reviews() -> list[AgentResult]:
    # Realistic review content cites `path:line`, not the author's own name, so
    # the anonymized block leaks nothing. (A reviewer that signs its own prose is
    # outside the orchestrator's control; anonymization governs the headings and
    # ordering we add, not arbitrary body text.)
    return [
        AgentResult("claude", "anthropic", True, "bug at src/a.py:1", 0.0),
        AgentResult("codex", "openai", True, "bug at src/b.py:2", 0.0),
        AgentResult("agy", "google", True, "bug at src/c.py:3", 0.0),
    ]


class AnonLabelTest(unittest.TestCase):
    def test_label_sequence(self) -> None:
        self.assertEqual([_anon_label(i) for i in range(3)], ["A", "B", "C"])

    def test_label_wraps_past_z(self) -> None:
        self.assertEqual(_anon_label(25), "Z")
        self.assertEqual(_anon_label(26), "AA")


class AnonymizePeersTest(unittest.TestCase):
    def test_no_identity_leaks_in_peer_block(self) -> None:
        reviews = _fake_reviews()
        text, mapping = _anonymize_peers(reviews, "claude", random.Random(1))
        for name in AGENT_NAMES:
            self.assertNotIn(name, text, f"agent name '{name}' leaked")
        for vendor in VENDOR_NAMES:
            self.assertNotIn(vendor, text, f"vendor '{vendor}' leaked")
        self.assertIn("Reviewer A", text)
        self.assertIn("Reviewer B", text)

    def test_own_review_excluded(self) -> None:
        reviews = _fake_reviews()
        text, mapping = _anonymize_peers(reviews, "codex", random.Random(1))
        # The debater (codex) must not see its own round-1 review content here;
        # it is passed separately as own_review.
        self.assertNotIn("src/b.py:2", text)
        self.assertNotIn("codex", mapping.values())
        # The two peers are present.
        self.assertEqual(set(mapping.values()), {"claude", "agy"})

    def test_mapping_recoverable(self) -> None:
        reviews = _fake_reviews()
        text, mapping = _anonymize_peers(reviews, "claude", random.Random(7))
        # Every emitted label maps back to a real agent, and the labelled
        # content matches that agent's review.
        for label, agent in mapping.items():
            self.assertIn(f"### {label}\n", text)
            review = next(r for r in reviews if r.agent == agent)
            self.assertIn(review.output, text)

    def test_same_seed_same_order_diff_seed_may_differ(self) -> None:
        reviews = _fake_reviews()
        a, _ = _anonymize_peers(reviews, "claude", random.Random(42))
        b, _ = _anonymize_peers(reviews, "claude", random.Random(42))
        self.assertEqual(a, b)  # deterministic for a fixed seed
        # Find a seed that yields a different ordering (overwhelmingly likely).
        differs = any(
            _anonymize_peers(reviews, "claude", random.Random(s))[0] != a
            for s in range(1, 50)
        )
        self.assertTrue(differs, "ordering never varied across seeds")

    def test_no_peers_placeholder(self) -> None:
        solo = [AgentResult("claude", "anthropic", True, "x", 0.0)]
        text, mapping = _anonymize_peers(solo, "claude", random.Random(1))
        self.assertEqual(mapping, {})
        self.assertIn("no other reviews", text)


class DebatePromptNoLeakTest(unittest.TestCase):
    """End-to-end: the actual debate prompt built by run_council leaks no names."""

    def _capture_debate_prompts(self, cfg: CouncilConfig) -> dict[str, str]:
        captured: dict[str, str] = {}
        real_run_phase = orchestrator._run_phase

        def capturing_run_phase(adapters, prompt_for, phase, parallel, **kwargs):
            if phase == "debate":
                captured.update(prompt_for)
            return real_run_phase(adapters, prompt_for, phase, parallel, **kwargs)

        orchestrator._run_phase = capturing_run_phase
        try:
            run_council(cfg, SAMPLE_DIFF, mock=True, seed=123)
        finally:
            orchestrator._run_phase = real_run_phase
        return captured

    def test_anonymized_debate_prompt_has_no_agent_or_vendor_names(self) -> None:
        cfg = _config()  # anonymize_debate defaults to True
        prompts_by_agent = self._capture_debate_prompts(cfg)
        self.assertTrue(prompts_by_agent)
        for debater, prompt in prompts_by_agent.items():
            # Isolate the peer block (between the UNTRUSTED_REVIEW sentinels).
            peer = prompt.split("<<<UNTRUSTED_REVIEW", 1)[1]
            # The identity we control is the per-peer HEADING. Assert no heading
            # carries an agent/vendor identity (the mock review *body* happens to
            # print its own name, which is reviewer-authored content outside the
            # orchestrator's control; anonymization governs labels + ordering).
            headings = [ln for ln in peer.splitlines() if ln.startswith("### ")]
            self.assertTrue(headings)
            for h in headings:
                self.assertTrue(
                    h.startswith("### Reviewer "),
                    f"non-anonymous heading in {debater}'s peer block: {h!r}",
                )
                for name in AGENT_NAMES:
                    self.assertNotIn(name, h)
                for vendor in VENDOR_NAMES:
                    self.assertNotIn(vendor, h)
            self.assertIn("### Reviewer A", peer)

    def test_escape_hatch_restores_identity_labeled_path(self) -> None:
        cfg = _config()
        cfg.anonymize_debate = False
        prompts_by_agent = self._capture_debate_prompts(cfg)
        # With anonymization off the legacy identity-labeled peers reappear.
        any_prompt = next(iter(prompts_by_agent.values()))
        peer = any_prompt.split("<<<UNTRUSTED_REVIEW", 1)[1]
        self.assertIn("(", peer)  # "### claude (anthropic)" style heading
        self.assertNotIn("Reviewer A", peer)

    def test_label_assignment_stable_per_run(self) -> None:
        # Same seed twice -> identical debate prompts (labels + order stable).
        cfg = _config()
        first = self._capture_debate_prompts(cfg)
        second = self._capture_debate_prompts(cfg)
        self.assertEqual(first, second)


class ReportStillUsesRealNamesTest(unittest.TestCase):
    def test_rendered_report_attributes_by_real_name(self) -> None:
        outcome = run_council(_config(), SAMPLE_DIFF, mock=True, seed=5)
        report = render(
            outcome.reviews,
            outcome.debate,
            outcome.synthesis,
            chair=outcome.chair,
            findings=outcome.findings,
            warnings=outcome.warnings,
            groups=outcome.groups,
            verify=outcome.verify,
        )
        for name in AGENT_NAMES:
            self.assertIn(name, report)


if __name__ == "__main__":
    unittest.main()
