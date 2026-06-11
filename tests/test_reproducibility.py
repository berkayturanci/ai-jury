"""Reproducibility & determinism controls (issue #41).

Offline: uses the mock pipeline (no live CLIs, no network). Proves the
acceptance criteria:

  - Same seed -> byte-identical mock report.
  - Report ordering is independent of thread-pool completion order (we force
    the round phases to return results SHUFFLED and assert the final, rendered
    order is the stable enabled-agent order regardless).
  - Run metadata records seed + config hash; same config -> same hash, changed
    config -> different hash.

LLM output is inherently model-variable; only the ORCHESTRATION around it is
made deterministic, which is exactly what these tests lock.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import orchestrator  # noqa: E402
from ai_jury.config import (  # noqa: E402
    DEFAULT_CONFIG,
    JuryConfig,
    _from_dict,
    config_hash,
)
from ai_jury.metadata import build_run_metadata  # noqa: E402
from ai_jury.orchestrator import run_jury  # noqa: E402
from ai_jury.report import render  # noqa: E402

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)


def _config() -> JuryConfig:
    return _from_dict(DEFAULT_CONFIG)


def _render_outcome(outcome) -> str:
    return render(
        outcome.reviews,
        outcome.debate,
        outcome.synthesis,
        chair=outcome.chair,
        findings=outcome.findings,
        warnings=outcome.warnings,
        groups=outcome.groups,
        verify=outcome.verify,
        context_mode=outcome.context_mode,
        redact_secrets=outcome.redact_secrets,
        redaction_count=outcome.redaction_count,
    )


class SeedDeterminismTest(unittest.TestCase):
    def test_same_seed_byte_identical_mock_report(self) -> None:
        cfg_a = _config()
        cfg_a.seed = 123
        cfg_b = _config()
        cfg_b.seed = 123

        report_a = _render_outcome(run_jury(cfg_a, SAMPLE_DIFF, mock=True))
        report_b = _render_outcome(run_jury(cfg_b, SAMPLE_DIFF, mock=True))
        self.assertEqual(report_a, report_b)

    def test_seed_via_kwarg_matches_seed_via_config(self) -> None:
        # The explicit seed kwarg and config.seed are interchangeable inputs to
        # the same shared run RNG, so both produce identical mock output.
        cfg = _config()
        cfg.seed = 7
        via_config = _render_outcome(run_jury(cfg, SAMPLE_DIFF, mock=True))

        cfg2 = _config()  # no config seed
        via_kwarg = _render_outcome(run_jury(cfg2, SAMPLE_DIFF, mock=True, seed=7))
        self.assertEqual(via_config, via_kwarg)


class OrderIndependenceTest(unittest.TestCase):
    """The key regression guard: report order must not depend on completion order."""

    def _run_with_phase_shuffle(self, reverse: bool):
        """Run the jury with ``_run_phase`` results returned in a non-canonical
        order, simulating thread-pool completions arriving out of order.
        """
        real_run_phase = orchestrator._run_phase

        def shuffling_run_phase(adapters, prompt_for, phase, parallel, **kwargs):
            results = real_run_phase(adapters, prompt_for, phase, parallel, **kwargs)
            # Genuinely permute the completion order. Reversal is a deterministic
            # but non-identity permutation, which is enough to prove the
            # orchestrator re-sorts rather than relying on arrival order.
            return list(reversed(results)) if reverse else results

        orchestrator._run_phase = shuffling_run_phase
        try:
            return run_jury(_config(), SAMPLE_DIFF, mock=True)
        finally:
            orchestrator._run_phase = real_run_phase

    def test_outcome_order_is_stable_under_shuffled_completion(self) -> None:
        normal = self._run_with_phase_shuffle(reverse=False)
        shuffled = self._run_with_phase_shuffle(reverse=True)

        # Canonical order is the enabled-agent order from the default config.
        expected = [a.name for a in _config().enabled_agents]
        self.assertEqual([r.agent for r in normal.reviews], expected)
        self.assertEqual([r.agent for r in shuffled.reviews], expected)
        self.assertEqual([r.agent for r in normal.debate], expected)
        self.assertEqual([r.agent for r in shuffled.debate], expected)

    def test_rendered_report_is_identical_under_shuffled_completion(self) -> None:
        normal = self._run_with_phase_shuffle(reverse=False)
        shuffled = self._run_with_phase_shuffle(reverse=True)
        self.assertEqual(_render_outcome(normal), _render_outcome(shuffled))


class ConfigHashMetadataTest(unittest.TestCase):
    def test_metadata_includes_seed_and_config_hash(self) -> None:
        cfg = _config()
        cfg.seed = 42
        meta = build_run_metadata(run_jury(cfg, SAMPLE_DIFF, mock=True), cfg)
        self.assertEqual(meta["seed"], 42)
        self.assertIsInstance(meta["config_hash"], str)
        self.assertEqual(len(meta["config_hash"]), 64)

    def test_same_config_same_hash(self) -> None:
        self.assertEqual(config_hash(_config()), config_hash(_config()))

    def test_changed_config_different_hash(self) -> None:
        base = _config()
        changed = _config()
        changed.rounds = base.rounds + 1
        self.assertNotEqual(config_hash(base), config_hash(changed))

    def test_seed_does_not_affect_config_hash(self) -> None:
        # The config hash describes settings independent of the chosen run seed.
        a = _config()
        a.seed = 1
        b = _config()
        b.seed = 999
        self.assertEqual(config_hash(a), config_hash(b))


if __name__ == "__main__":
    unittest.main()
