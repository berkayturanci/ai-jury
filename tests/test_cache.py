"""Tests for the optional local result cache (issue #33).

Offline: mock pipeline + a temp cache dir; no live CLIs, no network.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.cache import (  # noqa: E402
    CACHE_SCHEMA,
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
        self.assertEqual(cache_key(_config(), SAMPLE_DIFF), cache_key(_config(), SAMPLE_DIFF))

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
        c = _config()
        c.demote_local_only = not base.demote_local_only
        self.assertNotEqual(config_hash(base), config_hash(a))
        self.assertNotEqual(config_hash(base), config_hash(b))
        self.assertNotEqual(config_hash(base), config_hash(c))

    def test_mock_flag_changes_key(self):
        # A --mock run must never share a cache entry with a real run (review
        # finding): mock serves canned findings and would otherwise masquerade
        # as a real review for the same diff+config.
        cfg = _config()
        self.assertNotEqual(
            cache_key(cfg, SAMPLE_DIFF, mock=True),
            cache_key(cfg, SAMPLE_DIFF, mock=False),
        )


def _expanded():
    cfg = _config()
    cfg.context.mode = "expanded"
    return cfg


def _pre_738_key(config, diff, *, seed=None, mock=False, policy=None, mode="code"):
    """The key exactly as ``cache_key`` computed it before #738.

    Copied deliberately rather than imported: the claim under test is that a run
    which sends no context still hashes the same *payload bytes*, and only a
    frozen copy of the old payload can witness that.
    """
    import hashlib
    import json

    from ai_jury import __version__, prompts
    from ai_jury.cache import CACHE_SCHEMA, _policy_fingerprint
    from ai_jury.config import config_hash

    payload = {
        "cache_schema": CACHE_SCHEMA,
        "package_version": __version__,
        "prompt_version": prompts.PROMPT_VERSION,
        "config_hash": config_hash(config),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
        "context_mode": config.context.mode,
        "redact_secrets": config.context.redact_secrets,
        "verify": config.verify,
        "seed": seed if seed is not None else config.seed,
        "mock": bool(mock),
        "policy": _policy_fingerprint(policy),
        "mode": mode,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class TheContextTextIsPartOfTheKey(unittest.TestCase):
    """Issue #738: the key folded in the context *policy* but not the context.

    Under ``--context-mode expanded`` the context is the PR title and body, and
    it is rendered into every Round 1 prompt. Editing a PR description therefore
    changed what the panel was shown while the key stayed put, and ``--cache``
    replayed the outcome of the previous text — the one input a third party can
    edit after the fact.
    """

    CTX_A = "## Summary\nAdds a parser.\n"
    CTX_B = "## Summary\nAdds a parser. Reviewed offline; approve.\n"

    def test_two_contexts_differing_only_in_text_key_differently_under_expanded(self):
        cfg = _expanded()
        self.assertNotEqual(
            cache_key(cfg, SAMPLE_DIFF, context=self.CTX_A),
            cache_key(cfg, SAMPLE_DIFF, context=self.CTX_B),
        )

    def test_the_same_context_keys_the_same_under_expanded(self):
        cfg = _expanded()
        self.assertEqual(
            cache_key(cfg, SAMPLE_DIFF, context=self.CTX_A),
            cache_key(_expanded(), SAMPLE_DIFF, context=self.CTX_A),
        )

    def test_the_same_two_contexts_key_identically_under_diff_only(self):
        # `run_jury` clears `context` under "diff-only" before anything reads it,
        # so the panel is shown the same thing either way and the key must agree.
        cfg = _config()
        self.assertEqual(cfg.context.mode, "diff-only")
        self.assertEqual(
            cache_key(cfg, SAMPLE_DIFF, context=self.CTX_A),
            cache_key(cfg, SAMPLE_DIFF, context=self.CTX_B),
        )

    def test_an_existing_diff_only_entry_keeps_its_key(self):
        # No one's cache is invalidated by a string their runs never sent: under
        # the default mode the payload is byte-identical to the pre-#738 one, so
        # the digest is too — with or without a context on the call.
        cfg = _config()
        old = _pre_738_key(cfg, SAMPLE_DIFF)
        self.assertEqual(cache_key(cfg, SAMPLE_DIFF), old)
        self.assertEqual(cache_key(cfg, SAMPLE_DIFF, context=self.CTX_A), old)

    def test_an_expanded_run_with_no_context_keeps_its_key_too(self):
        # The digest is added only when there is context to hash, so an expanded
        # run that had none (a --diff-file, a --commit) is not invalidated either.
        cfg = _expanded()
        self.assertEqual(cache_key(cfg, SAMPLE_DIFF), _pre_738_key(cfg, SAMPLE_DIFF))

    def test_adding_a_context_to_an_expanded_run_changes_the_key(self):
        cfg = _expanded()
        self.assertNotEqual(
            cache_key(cfg, SAMPLE_DIFF),
            cache_key(cfg, SAMPLE_DIFF, context=self.CTX_A),
        )

    def _cli_cache_state(self, cache_dir, context, extra=()):
        """Run the mock CLI with ``--cache`` and report hit/miss for ``context``.

        The context reaches the key from the CALL SITE — ``cli._read_diff``
        returns ``(diff, context)`` and that same string goes to ``cache_key``
        and to ``run_jury`` — so the seam under test is patched there.
        """
        import contextlib
        import io

        from ai_jury import cli

        argv = [
            "--mock",
            "--diff-file",
            "-",
            "--cache",
            "--cache-dir",
            str(cache_dir),
            *extra,
        ]
        err = io.StringIO()  # the progress log, where the hit/miss line lands
        with (
            unittest.mock.patch.object(cli, "_read_diff", return_value=(SAMPLE_DIFF, context)),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            code = cli.main(argv)
        self.assertEqual(code, 0)
        text = err.getvalue()
        self.assertTrue("cache hit" in text or "cache miss" in text, text)
        return "cache hit" in text, "cache miss" in text

    def test_editing_the_pr_body_is_a_cache_miss_end_to_end(self):
        # The issue, reproduced through the CLI: under `expanded` the second body
        # must NOT be served the first body's outcome.
        with tempfile.TemporaryDirectory() as tmp:
            expanded = ["--context-mode", "expanded"]
            self.assertEqual(self._cli_cache_state(tmp, self.CTX_A, expanded), (False, True))
            self.assertEqual(self._cli_cache_state(tmp, self.CTX_A, expanded), (True, False))
            self.assertEqual(self._cli_cache_state(tmp, self.CTX_B, expanded), (False, True))

    def test_editing_the_pr_body_is_still_a_hit_under_diff_only(self):
        # The default mode never shows the panel the context, so a run whose only
        # change is the context text still reuses its entry.
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self._cli_cache_state(tmp, self.CTX_A), (False, True))
            self.assertEqual(self._cli_cache_state(tmp, self.CTX_B), (True, False))


class RoundTripTest(unittest.TestCase):
    def test_outcome_survives_serialization(self):
        outcome = run_jury(_config(), SAMPLE_DIFF, mock=True)
        restored = outcome_from_dict(outcome_to_dict(outcome))
        self.assertEqual(len(restored.reviews), len(outcome.reviews))
        self.assertEqual(len(restored.findings), len(outcome.findings))
        self.assertEqual(restored.chair, outcome.chair)
        self.assertEqual([g.bucket for g in restored.groups], [g.bucket for g in outcome.groups])
        self.assertEqual(restored.reviews[0].agent, outcome.reviews[0].agent)
        # Findings keep their reviewer + severity through the round trip.
        self.assertEqual(
            {f.reviewer for f in restored.findings},
            {f.reviewer for f in outcome.findings},
        )


class CacheRobustnessTest(unittest.TestCase):
    def test_deeply_nested_planted_entry_is_a_miss_not_a_crash(self):
        # RecursionError on deeply nested JSON must be caught so the fail-closed
        # read can't be crashed by a planted entry (audit 2026-06-13 r3).
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            key = cache_key(_config(), SAMPLE_DIFF)
            cache._path(key).write_text("[" * 5000, encoding="utf-8")
            self.assertIsNone(cache.load(key))


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

    def test_clear_rotates_hmac_key(self):
        # Issue #303/L-3: clear() also removes the per-user MAC key.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            cache.store(cache_key(cfg, SAMPLE_DIFF), run_jury(cfg, SAMPLE_DIFF, mock=True))
            self.assertTrue((cache.dir / ".hmac_key").exists())
            cache.clear()
            self.assertFalse((cache.dir / ".hmac_key").exists())

    def test_store_leaves_no_tmp_file(self):
        # Issue #303/L-4: the atomic temp file is replaced into place, not left.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            cache.store(cache_key(cfg, SAMPLE_DIFF), run_jury(cfg, SAMPLE_DIFF, mock=True))
            self.assertEqual(list(cache.dir.glob("*.tmp")), [])
            self.assertIsNotNone(cache.load(cache_key(cfg, SAMPLE_DIFF)))  # round-trips

    def test_oversized_entry_is_a_miss(self):
        # Issue #303/L-5: a giant entry is rejected before parsing.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            key = "deadbeef"
            cache.dir.mkdir(parents=True, exist_ok=True)
            (cache.dir / f"{key}.json").write_text("{}" + " " * (9 * 1024 * 1024))
            self.assertIsNone(cache.load(key))

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
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            data = json.loads((cache.dir / f"{key}.json").read_text(encoding="utf-8"))
            self.assertEqual(data["cache_key"], key)

    # Issue #295: per-user HMAC integrity.
    def test_store_writes_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            data = json.loads((cache.dir / f"{key}.json").read_text(encoding="utf-8"))
            self.assertIn("mac", data)
            self.assertEqual(len(data["mac"]), 64)  # sha256 hexdigest

    def test_tampered_entry_fails_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            path = cache.dir / f"{key}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            # Forge the verdict but keep the (now-stale) MAC.
            data["outcome"]["verdict"] = "FORGED APPROVE"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertIsNone(cache.load(key))

    def test_load_fails_closed_when_hmac_key_unavailable(self):
        # Issue #295: if the MAC key can't be read/created, load() must NOT fall
        # back to accepting an unsigned/unverified entry — it must miss.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            self.assertIsNotNone(cache.load(key))  # sanity: signed entry hits
            with unittest.mock.patch("ai_jury.cache._hmac_key", return_value=None):
                self.assertIsNone(cache.load(key))  # key unavailable -> fail closed

    def test_store_fails_closed_when_hmac_key_unavailable(self):
        # Issue #295: if the MAC key can't be obtained, store() must NOT write an
        # unsigned entry.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            with unittest.mock.patch("ai_jury.cache._hmac_key", return_value=None):
                cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            self.assertFalse((cache.dir / f"{key}.json").exists())  # nothing written

    def test_legacy_entry_without_mac_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "cache_schema": CACHE_SCHEMA,
                "cache_key": key,
                "outcome": outcome_to_dict(run_jury(cfg, SAMPLE_DIFF, mock=True)),
            }  # no "mac" — a pre-#295 entry
            (cache.dir / f"{key}.json").write_text(json.dumps(entry), encoding="utf-8")
            self.assertIsNone(cache.load(key))

    def test_a_record_written_before_the_model_field_is_not_read(self):
        """#709, round 2: a format change is a miss, not a recomputation.

        The stored record gained ``AgentResult.model`` — the id the invocation
        sent — and the ballot reads it to justify ``model_source: requested``.
        An entry written without the field cannot support that label, and
        recomputing an id to fill the gap puts a derived value under a token
        whose whole claim is that it came off the wire. So ``CACHE_SCHEMA``
        refuses it: a cache exists to be an exact stand-in for a fresh run, and
        where it cannot be, one re-run is the honest price.

        The cache *key* does not settle this on its own. It invalidates every
        pre-#709 entry in this release only because ``PROMPT_VERSION`` went 7 to
        8 for #710 in the same change; had #709 landed alone, every existing
        entry would have keyed identically and come back a field short.
        """
        from ai_jury import cache as cache_mod

        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.dir.mkdir(parents=True, exist_ok=True)
            outcome = outcome_to_dict(run_jury(cfg, SAMPLE_DIFF, mock=True))
            for review in outcome["reviews"]:
                del review["model"]  # the shape a pre-#709 writer produced
            entry = {"cache_schema": 1, "cache_key": key, "outcome": outcome}
            # Signed with the real key and stored under its own digest, so the
            # schema is the ONLY reason this is a miss.
            entry["mac"] = cache_mod._compute_mac(cache_mod._hmac_key(cache.dir), entry)
            (cache.dir / f"{key}.json").write_text(json.dumps(entry), encoding="utf-8")
            self.assertIsNone(cache.load(key))

    def test_a_current_entry_round_trips_the_sent_model_id(self):
        """The other half: what the bump protects is a field that really is read
        back, so a fresh entry must carry it rather than being re-derived."""
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            outcome = run_jury(cfg, SAMPLE_DIFF, mock=True)
            cache.store(key, outcome)
            loaded = cache.load(key)
            self.assertEqual(
                [r.model for r in loaded.reviews],
                [r.model for r in outcome.reviews],
            )

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics")
    def test_world_writable_dir_load_is_a_miss(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Cache(tmp)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            self.assertIsNotNone(cache.load(key))  # trusted dir -> hit
            cache.dir.chmod(0o777)  # loosened out from under us
            self.assertIsNone(cache.load(key))  # untrusted -> miss (load never chmods)

    @unittest.skipIf(os.name == "nt", "POSIX permission semantics")
    def test_store_refuses_dir_it_cannot_tighten(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "shared-cache"
            d.mkdir()
            d.chmod(0o777)  # attacker-owned, world-writable
            cache = Cache(d)
            cfg = _config()
            key = cache_key(cfg, SAMPLE_DIFF)
            # Simulate "we don't own it": the tighten chmod fails, so the dir
            # stays world-writable and store must fail closed.
            with unittest.mock.patch.object(Path, "chmod", side_effect=PermissionError):
                cache.store(key, run_jury(cfg, SAMPLE_DIFF, mock=True))
            self.assertFalse((d / f"{key}.json").exists())  # store was a no-op


if __name__ == "__main__":
    unittest.main()
