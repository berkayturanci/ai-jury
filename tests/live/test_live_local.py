"""Opt-in live smoke test for the local / open-weight adapter (issue #43).

Skipped entirely unless ``COUNCIL_LOCAL_LIVE=1`` is set AND a local
OpenAI-compatible server is reachable. It exercises the real HTTP path against a
local model (default: Ollama with ``qwen2.5-coder:7b``) — no cloud CLIs, no cost.

Run with::

    COUNCIL_LOCAL_LIVE=1 PYTHONPATH=src python -m unittest \
        tests.live.test_live_local

Override the target with ``COUNCIL_LOCAL_ENDPOINT`` and ``COUNCIL_LOCAL_MODEL``.
"""
from __future__ import annotations

import os
import unittest

from agent_review_council.adapters import LocalAdapter
from agent_review_council.config import AgentSpec
from agent_review_council.orchestrator import run_council

_ENABLED = os.environ.get("COUNCIL_LOCAL_LIVE") == "1"
_ENDPOINT = os.environ.get("COUNCIL_LOCAL_ENDPOINT", "http://localhost:11434/v1")
_MODEL = os.environ.get("COUNCIL_LOCAL_MODEL", "qwen2.5-coder:7b")

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,6 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)


def _spec():
    return AgentSpec(
        name="local", vendor="local", model=_MODEL, endpoint=_ENDPOINT, timeout=180
    )


@unittest.skipUnless(_ENABLED, "set COUNCIL_LOCAL_LIVE=1 to run the local live smoke test")
class LocalLiveSmokeTest(unittest.TestCase):
    def setUp(self):
        if not LocalAdapter(_spec()).available():
            self.skipTest(f"no local OpenAI-compatible server reachable at {_ENDPOINT}")

    def test_local_adapter_returns_a_review(self):
        result = LocalAdapter(_spec()).run("Reply with the single word: OK", phase="review")
        self.assertTrue(result.ok, f"local run failed: {result.error}")
        self.assertTrue(result.output.strip())

    def test_full_pipeline_with_local_only_panel(self):
        from agent_review_council.config import (
            CiConfig,
            ContextConfig,
            CouncilConfig,
            DiffConfig,
        )

        cfg = CouncilConfig(
            rounds=1, chair="local", verify=False, agents=[_spec()],
            ci=CiConfig(), context=ContextConfig(), diff=DiffConfig(),
        )
        outcome = run_council(cfg, SAMPLE_DIFF, mock=False)
        self.assertEqual(len(outcome.reviews), 1)
        self.assertTrue(outcome.reviews[0].ok)
        self.assertIsNotNone(outcome.synthesis)


if __name__ == "__main__":
    unittest.main()
