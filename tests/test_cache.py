"""Tests for the optional local result cache (issue #33).

Offline: mock pipeline + a temp cache dir; no live CLIs, no network.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.cache import (  # noqa: E402
    Cache,
    cache_key,
    outcome_from_dict,
    outcome_to_dict,
)
from ai_jury.config import DEFAULT_CONFIG, _from_dict  # noqa: E402
from ai_jury.orchestrator import run_jury  # noqa: E402

SAMPLE_DIFF = (
    "diff --git a/src/example.py b/src/example.py\n"
    "@@ -1,3 +1,4 @@\n"
    "+def parse(x):\n"
    "+    return int(x)\n"
)


def _config():
    return _from_dict(DEFAULT_CONFIG)


class CacheKeyTest(unittest.TestCase):
    def test_same_inputs_same_key(self):
        self.assertEqual(
            cache_key(_config(), SAMPLE_DIFF), cache_key(_config(), SAMPLE_DIFF)
        )

    def test_diff_change_changes_key(self):
        self.assertNotEqual(
            cache_key(_config(), SAMPLE_DIFF), cache_key(_config(), SAMPLE_DIFF + "\n+x")
        )

    def test_config_change_changes_key(self):
        cfg2 = _config()
        cfg2.rounds = 1
        self.assertNotEqual(cache_key(_config(), SAMPLE_DIFF), cache_key(cfg2, SAMPLE_DIFF))

    def test_seed_change_changes_key(self):
        cfg2 = _config()
        cfg2.seed = 99
        self.assertNotEqual(cache_key(_config(), SAMPLE_DIFF), cache_key(cfg2, SAMPLE_DIFF))

    def test_policy_changes_key(self):
        # Issue #122: a repository review policy is injected into the prompts, so
        # it must be part of the cache key (and an empty policy must not change it).
        from ai_jury.policy import ReviewPolicy

        cfg = _config()
        self.assertEqual(
            cache_key(cfg, SAMPLE_DIFF), cache_key(cfg, SAMPLE_DIFF, policy=ReviewPolicy())
        )
        self.assertNotEqual(
            cache_key(cfg, SAMPLE_DIFF),
            cache_key(cfg, SAMPLE_DIFF, policy=ReviewPolicy(high_risk_paths=["src/pay.py"])),
        )

    def test_config_hash_covers_orchestration_toggles(self):
        # Issue #122: anonymize_debate / prefer_non_reviewer_chair change how a run
        # is orchestrated, so config_hash must include them.
        from ai_jury.config import config_hash

        base = _config()
        a = _config()
        a.anonymize_debate = not base.anonymize_debate
        b = _config()
        b.prefer_non_reviewer_chair = not base.prefer_non_reviewer_chair
        self.assertNotEqual(config_hash(base), config_hash(a))
        self.assertNotEqual(config_hash(base), config_hash(b))

    def test_mock_flag_changes_key(self):
        # A --mock run must never share a cache entry with a real run (review
        # finding): mock serves canned findings and would otherwise masquerade
        # as a real review for the same diff+config.
        cfg = _config()
        self.assertNotEqual(
            cache_key(cfg, SAMPLE_DIFF, mock=True),
            cache_key(cfg, SAMPLE_DIFF, mock=False),
        )


class RoundTripTest(unittest.TestCase):
    def test_outcome_survives_serialization(self):
        outcome = run_jury(_config(), SAMPLE_DIFF, mock=True)
        restored = outcome_from_dict(outcome_to_dict(outcome))
        self.assertEqual(len(restored.reviews), len(outcome.reviews))
        self.assertEqual(len(restored.findings), len(outcome.findings))
        self.assertEqual(restored.chair, outcome.chair)
        self.assertEqual(
            [g.bucket for g in restored.groups], [g.bucket for g in outcome.groups]
        )
        self.assertEqual(restored.reviews[0].agent, outcome.reviews[0].agent)
        # Findings keep their reviewer + severity through the round trip.
        self.assertEqual(
            {f.reviewer for f in restored.findings},
            {f.reviewer for f in outcome.findings},
        )


class CacheHitMissTest(unittest.TestCase):
    def test_miss_then_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            self.assertIsNone(cache.load(key))  # miss

            outcome = run_jury(cfg, SAMPLE_DIFF, mock=True)
            self.assertFalse(outcome.from_cache)
            cache.store(key, outcome)

            hit = cache.load(key)  # hit
            self.assertIsNotNone(hit)
            self.assertTrue(hit.from_cache)
            self.assertEqual(len(hit.findings), len(outcome.findings))

    def test_invalidation_on_diff_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            cache.store(cache_key(cfg, SAMPLE_DIFF), run_jury(cfg, SAMPLE_DIFF, mock=True))
            # A changed diff yields a different key -> miss (invalidation).
            self.assertIsNone(cache.load(cache_key(cfg, SAMPLE_DIFF + "\n+more")))

    def test_clear_removes_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            cache.store(cache_key(cfg, SAMPLE_DIFF), run_jury(cfg, SAMPLE_DIFF, mock=True))
            self.assertEqual(cache.clear(), 1)
            self.assertIsNone(cache.load(cache_key(cfg, SAMPLE_DIFF)))

    def test_corrupt_entry_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            key = "deadbeef"
            cache.dir.mkdir(parents=True, exist_ok=True)
            (cache.dir / f"{key}.json").write_text("{not json", encoding="utf-8")
            self.assertIsNone(cache.load(key))

    def test_key_mismatch_is_a_miss(self):
        # Issue #293/F-10: an entry whose embedded cache_key doesn't match the
        # filename/key (e.g. a forged verdict copied from another key) is a miss.
        import json

        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            # Tamper: rewrite the entry to claim a different cache_key.
            path = cache.dir / f"{key}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["cache_key"] = "someone-elses-key"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(cache.load(key))

    def test_store_embeds_cache_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            import json

            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            data = json.loads((cache.dir / f"{key}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["cache_key"], key)


if __name__ == "__main__":
    unittest.main()
