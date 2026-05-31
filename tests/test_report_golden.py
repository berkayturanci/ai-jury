"""Golden-file (snapshot) tests for the markdown report renderer.

The markdown report produced by ``report.render`` is user-facing, so unintended
formatting drift should fail CI while *intentional* changes are reviewable as
fixture diffs in ``tests/golden/``.

Each test renders a report for one scenario, normalizes non-deterministic bits,
and compares the result against the committed fixture for that scenario.

Determinism
-----------
``AgentResult.duration_s`` is the only non-deterministic part of the rendered
report (it is printed as ``{r.duration_s:.0f}s``). We neutralize it two ways:
the mock/orchestrator pipeline already emits ``duration_s=0.0``, and every
rendered report is additionally passed through ``_normalize`` which rewrites any
``<digits>s`` status token to ``0s`` before comparison or writing. This makes
the goldens reproducible regardless of timing.

Regenerating goldens
--------------------
When you intentionally change the report format, regenerate the fixtures::

    UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest \
        tests.test_report_golden

That rewrites the ``.md`` files under ``tests/golden/`` with the current output
instead of asserting. Review the resulting fixture diff like any other change,
then commit it. Running the suite normally (without the env var) asserts the
rendered output matches the committed fixtures.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council.adapters import AgentResult  # noqa: E402
from agent_review_council.config import (  # noqa: E402
    DEFAULT_CONFIG,
    CouncilConfig,
    _from_dict,
)
from agent_review_council.metadata import build_run_metadata  # noqa: E402
from agent_review_council.orchestrator import run_council  # noqa: E402
from agent_review_council.report import render  # noqa: E402

GOLDEN_DIR = Path(__file__).parent / "golden"
UPDATE = os.environ.get("UPDATE_GOLDEN") == "1"

SAMPLE_DIFF = """diff --git a/src/example.py b/src/example.py
@@ -1,3 +1,6 @@
+def parse(x):
+    return int(x)
"""

# Matches the duration status token rendered as ``{duration_s:.0f}s`` so timing
# never leaks into a golden file even if an AgentResult carries a real duration.
_DURATION_RE = re.compile(r"\b\d+s\b")


def _config() -> CouncilConfig:
    return _from_dict(DEFAULT_CONFIG)


def _normalize(report: str) -> str:
    """Strip the only non-deterministic token (durations) from a report."""
    return _DURATION_RE.sub("0s", report)


class ReportGoldenTest(unittest.TestCase):
    maxDiff = None

    def _check(self, name: str, report: str) -> None:
        normalized = _normalize(report)
        path = GOLDEN_DIR / f"{name}.md"
        if UPDATE:
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(normalized, encoding="utf-8")
            return
        self.assertTrue(
            path.exists(),
            f"golden file missing: {path}\n"
            f"Regenerate with: UPDATE_GOLDEN=1 PYTHONPATH=src "
            f"python3 -m unittest tests.test_report_golden",
        )
        expected = path.read_text(encoding="utf-8")
        self.assertEqual(
            normalized,
            expected,
            f"\nReport rendering drifted from golden file {path}.\n"
            f"If this change is intentional, regenerate the fixtures with:\n"
            f"  UPDATE_GOLDEN=1 PYTHONPATH=src python3 -m unittest "
            f"tests.test_report_golden\n"
            f"then review the fixture diff.",
        )

    def test_full_report(self):
        """Standard full council report: 3 agents, two rounds, structured findings."""
        cfg = _config()
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        report = render(
            outcome.reviews,
            outcome.debate,
            outcome.synthesis,
            chair=outcome.chair,
            findings=outcome.findings,
            warnings=outcome.warnings,
            groups=outcome.groups,
            context_mode=outcome.context_mode,
            redact_secrets=outcome.redact_secrets,
            redaction_count=outcome.redaction_count,
            metadata=build_run_metadata(outcome, cfg),
        )
        self._check("full_report", report)

    def test_single_round_report(self):
        """Single-round report: rounds=1 means no debate (Round 2) section."""
        cfg = _config()
        cfg.rounds = 1
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        self.assertEqual(outcome.debate, [])
        report = render(
            outcome.reviews,
            outcome.debate,
            outcome.synthesis,
            chair=outcome.chair,
            findings=outcome.findings,
            warnings=outcome.warnings,
            groups=outcome.groups,
            context_mode=outcome.context_mode,
            redact_secrets=outcome.redact_secrets,
            redaction_count=outcome.redaction_count,
        )
        self._check("single_round_report", report)

    def test_verified_finding_report(self):
        """Verified-finding report: verify=True adds Verification + group statuses."""
        cfg = _config()
        cfg.verify = True
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        self.assertIsNotNone(outcome.verify)
        report = render(
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
            metadata=build_run_metadata(outcome, cfg),
        )
        self._check("verified_finding_report", report)

    def test_failed_agent_report(self):
        """Failed-agent report: one reviewer errored (ok=False) in both rounds."""
        reviews = [
            AgentResult("claude", "anthropic", True, "claude review body.", 0.0),
            AgentResult(
                "codex", "openai", False, "", 0.0, error="exit 1: rate limited"
            ),
            AgentResult("agy", "google", True, "agy review body.", 0.0),
        ]
        debate = [
            AgentResult("claude", "anthropic", True, "claude debate body.", 0.0),
            AgentResult(
                "agy", "google", False, "", 0.0, error="timed out after 600s"
            ),
        ]
        synthesis = AgentResult(
            "claude", "anthropic", True, "## Verdict\nAPPROVE.", 0.0
        )
        report = render(
            reviews,
            debate,
            synthesis,
            chair="claude",
            warnings=["codex: could not parse JSON findings block"],
        )
        self._check("failed_agent_report", report)

    def test_missing_agent_report(self):
        """Skipped/missing-agent report: a configured agent never ran (synthesis failed)."""
        reviews = [
            AgentResult("claude", "anthropic", True, "claude review body.", 0.0),
            AgentResult("agy", "google", True, "agy review body.", 0.0),
        ]
        # No debate (e.g. only the chair would remain after a skip) and the chair
        # synthesis itself failed.
        synthesis = AgentResult(
            "claude", "anthropic", False, "", 0.0, error="command not found on PATH: claude"
        )
        report = render(
            reviews,
            [],
            synthesis,
            chair="claude",
        )
        self._check("missing_agent_report", report)


if __name__ == "__main__":
    unittest.main()
