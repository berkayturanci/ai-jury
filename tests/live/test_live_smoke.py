"""Opt-in live smoke tests for the real native agent CLI adapters.

Unit tests only exercise :class:`MockAdapter`, so the real ``claude``,
``codex`` and ``agy`` CLIs are never invoked. A change to argv format,
stdin handling, or output capture in the concrete adapters therefore goes
unnoticed until a live run.

These smoke tests close that gap by running a trivial real review prompt
through each *installed* adapter. They are intentionally:

* **Opt-in.** The whole suite is skipped unless ``COUNCIL_LIVE=1`` is set
  in the environment, so they never run in the default test suite or CI.
* **Per-agent skippable.** An agent whose CLI is not on ``PATH`` is skipped
  individually, so a machine with only ``claude`` installed still does
  something useful.
* **Cheap and fast.** The prompt is a tiny two-line diff and each adapter
  runs under a short timeout override so a hung CLI fails fast.

Run them with::

    make live-smoke
    # or
    COUNCIL_LIVE=1 PYTHONPATH=src python -m unittest discover -s tests -v
"""

from __future__ import annotations

import dataclasses
import os
import unittest

from agent_review_council.adapters import make_adapter
from agent_review_council.config import DEFAULT_CONFIG, _from_dict

# Short per-agent timeout so a hung CLI fails fast instead of blocking the
# whole suite for the production default (600s).
LIVE_TIMEOUT_S = 120

# A tiny, cheap diff to review. Kept deliberately small to minimise token
# spend and wall-clock time during live runs.
SMOKE_PROMPT = (
    "Briefly review this trivial diff and reply in one short sentence.\n"
    "```diff\n"
    "--- a/greet.py\n"
    "+++ b/greet.py\n"
    "@@\n"
    "-print('hi')\n"
    "+print('hello')\n"
    "```\n"
)


@unittest.skipUnless(
    os.environ.get("COUNCIL_LIVE") == "1",
    "live smoke tests disabled; set COUNCIL_LIVE=1",
)
class LiveSmokeTest(unittest.TestCase):
    """Run a trivial real review through each installed native CLI."""

    def _run_agent(self, raw_spec: dict) -> None:
        # _from_dict expects the raw TOML shape with an "agent" array.
        config = _from_dict({"agent": [raw_spec]})
        spec = config.agents[0]
        # Override the timeout so a hung CLI fails fast.
        spec = dataclasses.replace(spec, timeout=LIVE_TIMEOUT_S)
        adapter = make_adapter(spec, mock=False)

        if not adapter.available():
            self.skipTest(f"CLI not installed: {spec.command}")

        result = adapter.run(SMOKE_PROMPT, phase="review")

        self.assertIsNone(
            result.error_code,
            msg=f"{spec.name}: error_code={result.error_code} error={result.error!r}",
        )
        self.assertTrue(
            result.ok,
            msg=f"{spec.name}: run failed: {result.error!r}",
        )
        self.assertTrue(
            result.output.strip(),
            msg=f"{spec.name}: empty output",
        )


def _make_test(raw_spec: dict):
    def _test(self: LiveSmokeTest) -> None:
        self._run_agent(raw_spec)

    return _test


# Generate one test method per agent in the default config, so the suite
# stays in sync with the real adapter set. Each agent is skipped
# individually when its CLI is not installed.
for _raw in DEFAULT_CONFIG["agent"]:
    setattr(
        LiveSmokeTest,
        f"test_{_raw['name']}_smoke",
        _make_test(dict(_raw)),
    )


if __name__ == "__main__":
    unittest.main()
