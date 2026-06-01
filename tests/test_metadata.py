"""Tests for run metadata and cost-awareness reporting.

Offline: uses the mock pipeline (no live CLIs, no network). Asserts the
machine-readable metadata dict's shape, that it reflects config, and crucially
that no secret/prompt/diff/agent-output text leaks into it or its JSON sidecar.
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council.config import (  # noqa: E402
    DEFAULT_CONFIG,
    CouncilConfig,
    _from_dict,
)
from agent_review_council.metadata import build_run_metadata  # noqa: E402
from agent_review_council.orchestrator import run_council  # noqa: E402
from agent_review_council.report import render  # noqa: E402

SECRET = "sk-SUPERSECRETxyz1234567890"
PROMPT_MARKER = "RAW-PROMPT-DIFF-TEXT-MARKER"

# The mock review output keys structured findings off src/example.py.
SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)


def _config() -> CouncilConfig:
    return _from_dict(DEFAULT_CONFIG)


class MetadataShapeTest(unittest.TestCase):
    def test_keys_and_types(self) -> None:
        cfg = _config()
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        meta = build_run_metadata(outcome, cfg)

        expected_keys = {
            "schema_version",
            "agents",
            "rounds_executed",
            "from_cache",
            "stop_reason",
            "skipped",
            "retried",
            "budget_exhausted",
            "execution",
            "verify_enabled",
            "context_mode",
            "redact_secrets",
            "redaction_count",
            "seed",
            "config_hash",
            "classification",
            "total_wall_clock_s",
            "cost_signal",
            "generated_at",
        }
        self.assertEqual(set(meta.keys()), expected_keys)

        self.assertIsInstance(meta["schema_version"], int)
        self.assertIsInstance(meta["agents"], list)
        self.assertIsInstance(meta["rounds_executed"], int)
        self.assertIsInstance(meta["verify_enabled"], bool)
        self.assertIsInstance(meta["context_mode"], str)
        self.assertIsInstance(meta["redact_secrets"], bool)
        self.assertIsInstance(meta["redaction_count"], int)
        # seed is None when unseeded, an int when set; config_hash is a
        # 64-char hex SHA-256 digest.
        self.assertIsNone(meta["seed"])
        self.assertIsInstance(meta["config_hash"], str)
        self.assertEqual(len(meta["config_hash"]), 64)
        self.assertIsInstance(meta["total_wall_clock_s"], float)
        self.assertEqual(meta["cost_signal"], "wall-clock-proxy")
        # generated_at must be a valid ISO timestamp.
        datetime.fromisoformat(meta["generated_at"])

    def test_per_agent_duration_and_status(self) -> None:
        cfg = _config()
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        meta = build_run_metadata(outcome, cfg)

        self.assertEqual(len(meta["agents"]), len(outcome.reviews))
        for agent in meta["agents"]:
            self.assertIn("name", agent)
            self.assertIn("vendor", agent)
            self.assertIn(agent["status"], {"ok", "failed"})
            self.assertIsInstance(agent["duration_s"], float)
            self.assertIn("error_code", agent)  # present (None when ok)

        names = {a["name"] for a in meta["agents"]}
        self.assertEqual(names, {"claude", "codex", "agy"})

    def test_failed_agent_status_and_error_code(self) -> None:
        cfg = _config()
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        # Force one reviewer to look failed.
        outcome.reviews[1].ok = False
        outcome.reviews[1].error_code = "nonzero_exit"
        meta = build_run_metadata(outcome, cfg)
        failed = [a for a in meta["agents"] if a["status"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["error_code"], "nonzero_exit")

    def test_rounds_reflect_config(self) -> None:
        single = _config()
        single.rounds = 1
        meta_single = build_run_metadata(
            run_council(single, SAMPLE_DIFF, mock=True), single
        )
        self.assertEqual(meta_single["rounds_executed"], 1)

        multi = _config()
        multi.rounds = 2
        meta_multi = build_run_metadata(
            run_council(multi, SAMPLE_DIFF, mock=True), multi
        )
        self.assertEqual(meta_multi["rounds_executed"], 2)

    def test_verify_reflects_config(self) -> None:
        off = _config()
        off.verify = False
        self.assertFalse(
            build_run_metadata(
                run_council(off, SAMPLE_DIFF, mock=True), off
            )["verify_enabled"]
        )

        on = _config()
        on.verify = True
        self.assertTrue(
            build_run_metadata(
                run_council(on, SAMPLE_DIFF, mock=True), on
            )["verify_enabled"]
        )


class MetadataNoSecretsTest(unittest.TestCase):
    def _outcome_with_secret(self):
        cfg = _config()
        # Disable redaction so the secret would survive into outputs if copied.
        cfg.context.redact_secrets = False
        outcome = run_council(
            cfg, f"{PROMPT_MARKER} {SECRET}\n{SAMPLE_DIFF}", mock=True
        )
        # Inject a fake secret + prompt marker into agent output/error to be
        # defensive: the metadata must still never carry them.
        for r in outcome.reviews:
            r.output = f"{r.output}\n{SECRET}\n{PROMPT_MARKER}"
            r.error = SECRET
        return outcome, cfg

    def test_no_secret_or_prompt_in_metadata(self) -> None:
        outcome, cfg = self._outcome_with_secret()
        meta = build_run_metadata(outcome, cfg)
        serialized = json.dumps(meta)
        self.assertNotIn(SECRET, serialized)
        self.assertNotIn(PROMPT_MARKER, serialized)

    def test_no_secret_in_rendered_metadata_section(self) -> None:
        outcome, cfg = self._outcome_with_secret()
        meta = build_run_metadata(outcome, cfg)
        # Render ONLY the metadata section (empty body) so we isolate it from the
        # agent-output sections of the full report.
        report = render([], [], None, chair="", metadata=meta)
        self.assertIn("## Run metadata", report)
        self.assertNotIn(SECRET, report)
        self.assertNotIn(PROMPT_MARKER, report)


class MetadataJsonSidecarTest(unittest.TestCase):
    def test_json_round_trips(self) -> None:
        cfg = _config()
        cfg.verify = True
        outcome = run_council(cfg, SAMPLE_DIFF, mock=True)
        meta = build_run_metadata(outcome, cfg)

        text = json.dumps(meta, indent=2)
        restored = json.loads(text)
        self.assertEqual(restored["schema_version"], meta["schema_version"])
        self.assertEqual(len(restored["agents"]), len(meta["agents"]))
        self.assertEqual(restored["cost_signal"], "wall-clock-proxy")
        self.assertEqual(restored["verify_enabled"], True)


if __name__ == "__main__":
    unittest.main()
