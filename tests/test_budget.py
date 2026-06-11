"""Tests for the run budget, retries, and partial-result policy (issue #30).

Offline: uses mock adapters and small custom adapters; no live CLIs, no network.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import orchestrator  # noqa: E402
from ai_jury.adapters import (  # noqa: E402
    ERR_AUTH_REQUIRED,
    ERR_TIMEOUT,
    AgentResult,
    MockAdapter,
)
from ai_jury.config import DEFAULT_CONFIG, _from_dict  # noqa: E402
from ai_jury.metadata import build_run_metadata  # noqa: E402
from ai_jury.orchestrator import RunBudget, _run_with_retry, run_jury  # noqa: E402
from ai_jury.report import _metadata_block  # noqa: E402

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)


def _config():
    return _from_dict(DEFAULT_CONFIG)


class RunBudgetTest(unittest.TestCase):
    def test_no_caps_returns_none(self):
        b = RunBudget(None, None)
        self.assertIsNone(b.call_timeout())
        self.assertFalse(b.expired())
        self.assertIsNone(b.remaining())

    def test_phase_cap_applies(self):
        b = RunBudget(None, 30)
        self.assertEqual(b.call_timeout(), 30)

    def test_total_cap_bounds_call(self):
        b = RunBudget(45, None)
        # Remaining is ~45 right after construction; the call timeout is the
        # floor of remaining (never below 1).
        self.assertLessEqual(b.call_timeout(), 45)
        self.assertGreaterEqual(b.call_timeout(), 1)

    def test_min_of_phase_and_total(self):
        b = RunBudget(45, 10)
        self.assertEqual(b.call_timeout(), 10)


class _FlakyAdapter(MockAdapter):
    """Fails with a retryable timeout for the first ``fail_times`` calls."""

    def __init__(self, spec, fail_times, error_code=ERR_TIMEOUT):
        super().__init__(spec)
        self._fail_times = fail_times
        self._calls = 0
        self._error_code = error_code

    def run(self, prompt, phase="review", timeout=None):
        self._calls += 1
        if self._calls <= self._fail_times:
            return AgentResult(
                self.name,
                self.spec.vendor,
                False,
                "",
                0.0,
                error=f"transient {self._calls}",
                error_code=self._error_code,
            )
        return super().run(prompt, phase=phase, timeout=timeout)


class RetryTest(unittest.TestCase):
    def _adapter(self, fail_times, error_code=ERR_TIMEOUT):
        spec = _config().enabled_agents[0]
        return _FlakyAdapter(spec, fail_times, error_code)

    def test_retries_transient_failure_until_success(self):
        adapter = self._adapter(fail_times=2)
        result = _run_with_retry(
            adapter, "p", "review", RunBudget(None, None), retries=3, log=lambda _m: None
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.attempts, 3)

    def test_retries_bounded_by_count(self):
        adapter = self._adapter(fail_times=5)
        result = _run_with_retry(
            adapter, "p", "review", RunBudget(None, None), retries=2, log=lambda _m: None
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 3)  # 1 try + 2 retries

    def test_non_retryable_failure_is_not_retried(self):
        adapter = self._adapter(fail_times=5, error_code=ERR_AUTH_REQUIRED)
        result = _run_with_retry(
            adapter, "p", "review", RunBudget(None, None), retries=5, log=lambda _m: None
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 1)

    def test_default_zero_retries_runs_once(self):
        adapter = self._adapter(fail_times=1)
        result = _run_with_retry(
            adapter, "p", "review", RunBudget(None, None), retries=0, log=lambda _m: None
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.attempts, 1)


class PartialResultTest(unittest.TestCase):
    def test_skipped_agents_recorded_in_outcome_and_metadata(self):
        # A non-mock run where one agent's CLI does not exist: it is skipped, not
        # fatal, and surfaces in the outcome + metadata + report.
        cfg = _from_dict(
            {
                "jury": {"chair": "real", "verify": False, "rounds": 1},
                "agent": [
                    {"name": "real", "vendor": "anthropic", "command": "claude"},
                    {
                        "name": "ghost",
                        "vendor": "openai",
                        "command": "definitely-not-a-real-cli-xyz",
                    },
                ],
            }
        )
        # Force the "real" agent to be available + deterministic by using mock for
        # it only: simplest is to run mock=False but stub availability. Instead we
        # exercise the skip path directly via a custom adapter set.
        real_make = orchestrator.make_adapter

        def make(spec, mock=False):
            del mock  # signature-compatible stub; mode is fixed per agent here
            if spec.name == "ghost":
                # Unavailable adapter (command missing) -> skipped.
                return real_make(spec, mock=False)
            return MockAdapter(spec)

        orchestrator.make_adapter = make
        try:
            outcome = run_jury(cfg, SAMPLE_DIFF, mock=False)
        finally:
            orchestrator.make_adapter = real_make

        skipped_names = [name for name, _reason in outcome.skipped]
        self.assertIn("ghost", skipped_names)
        meta = build_run_metadata(outcome, cfg)
        self.assertEqual([s["name"] for s in meta["skipped"]], ["ghost"])
        block = "\n".join(_metadata_block(meta))
        self.assertIn("skipped agents (never ran)", block)
        self.assertIn("ghost", block)

    def test_retried_agent_surfaced_in_report(self):
        meta = {
            "rounds_executed": 1,
            "verify_enabled": False,
            "context_mode": "diff-only",
            "total_wall_clock_s": 0.0,
            "retried": ["codex"],
            "agents": [
                {
                    "name": "codex",
                    "vendor": "openai",
                    "status": "ok",
                    "duration_s": 0.0,
                    "error_code": None,
                    "attempts": 2,
                },
            ],
        }
        block = "\n".join(_metadata_block(meta))
        self.assertIn("retried agents: codex", block)
        self.assertIn("2 attempts", block)


if __name__ == "__main__":
    unittest.main()
