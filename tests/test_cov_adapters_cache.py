"""Offline coverage for adapters.py and cache.py error/edge branches.

All subprocess and HTTP access is mocked; the cache tests use tempfile dirs.
Targets the uncovered lines:
  adapters.py: 330 (AgyAdapter model arg), 357 (list_local_models non-list
    'data'), 435-439 (LocalAdapter.available HTTPError + generic Exception),
    442-443 (LocalAdapter.detect_capabilities), 479-480 (run HTTPError detail
    read fallback).
  cache.py: 122 (_agent_result None), 150 (_hit), 210 (load schema mismatch),
    227 (clear on missing dir).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import unittest.mock as mock
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import adapters  # noqa: E402
from ai_jury import cache as cache_mod  # noqa: E402
from ai_jury.config import AgentSpec  # noqa: E402


def _spec(**kw):
    base = {"name": "claude", "vendor": "anthropic", "command": "claude"}
    base.update(kw)
    return AgentSpec(**base)


def _local_spec(**kw):
    base = {
        "name": "qwen",
        "vendor": "local",
        "command": "",
        "model": "qwen2.5-coder:7b",
        "endpoint": "http://localhost:11434/v1",
    }
    base.update(kw)
    return AgentSpec(**base)


class _Resp:
    """Minimal context-manager stand-in for an urlopen response."""

    def __init__(self, body, status=200):
        self._b = body
        self.status = status

    def read(self, *args):
        return self._b.encode("utf-8")

    def close(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class AdaptersCoverageTests(unittest.TestCase):
    def test_agy_argv_with_model(self):
        # AgyAdapter.build_argv appends --model when a model is set; the mandatory
        # --sandbox is injected by the read-only enforcement (issue #288).
        a = adapters.AgyAdapter(
            _spec(name="agy", vendor="google", command="agy", model="gemini-x")
        )
        self.assertEqual(
            a.build_argv("P"),
            ["agy", "--print", "P", "--model", "gemini-x", "--sandbox"],
        )

    def test_list_local_models_data_not_list(self):
        # Line 357: a well-formed dict whose "data" is not a list -> [].
        body = json.dumps({"data": {"not": "a list"}})
        with mock.patch("ai_jury.adapters._open", return_value=_Resp(body)):
            self.assertEqual(adapters.list_local_models("http://x/v1"), [])

    def test_list_local_models_missing_data_key(self):
        # Same branch (357): dict with no "data" key at all -> [].
        body = json.dumps({"object": "list"})
        with mock.patch("ai_jury.adapters._open", return_value=_Resp(body)):
            self.assertEqual(adapters.list_local_models("http://x/v1"), [])

    def test_list_local_models_happy_path(self):
        body = json.dumps(
            {"data": [{"id": "m1"}, {"no": "id"}, {"id": "m2"}, "junk"]}
        )
        with mock.patch("ai_jury.adapters._open", return_value=_Resp(body)):
            self.assertEqual(
                adapters.list_local_models("http://x/v1"), ["m1", "m2"]
            )

    def test_list_local_models_unreachable(self):
        with mock.patch(
            "ai_jury.adapters._open",
            side_effect=urllib.error.URLError("refused"),
        ):
            self.assertEqual(adapters.list_local_models("http://x/v1"), [])

    def test_available_ok(self):
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch("ai_jury.adapters._open", return_value=_Resp("{}", status=200)):
            self.assertTrue(a.available())

    def test_available_http_error_4xx_means_up(self):
        # Lines 435-437: a 4xx HTTPError still means the server is up.
        err = urllib.error.HTTPError("u", 404, "not found", {}, None)
        self.addCleanup(err.close)
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch("ai_jury.adapters._open", side_effect=err):
            self.assertTrue(a.available())

    def test_available_http_error_5xx_means_down(self):
        # Lines 435-437: a 5xx HTTPError means the server is unhealthy.
        err = urllib.error.HTTPError("u", 503, "down", {}, None)
        self.addCleanup(err.close)
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch("ai_jury.adapters._open", side_effect=err):
            self.assertFalse(a.available())

    def test_available_unreachable(self):
        # Lines 438-439: any other failure -> not available.
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch(
            "ai_jury.adapters._open",
            side_effect=urllib.error.URLError("refused"),
        ):
            self.assertFalse(a.available())

    def test_detect_capabilities_reachable(self):
        # Lines 442-443: reachable server -> CAP_OK, no warnings.
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch.object(a, "available", return_value=True):
            caps = a.detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_OK)
        self.assertEqual(caps["warnings"], [])
        self.assertIn("localhost:11434", caps["raw_version_output"])

    def test_detect_capabilities_unreachable(self):
        # Lines 442-443: unreachable -> CAP_UNAVAILABLE with a warning.
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch.object(a, "available", return_value=False):
            caps = a.detect_capabilities()
        self.assertEqual(caps["status"], adapters.CAP_UNAVAILABLE)
        self.assertEqual(len(caps["warnings"]), 1)

    def test_run_http_error_detail_read_fails(self):
        # Lines 479-480: exc.read() raises -> fall back to exc.reason.
        class _BadHTTPError(urllib.error.HTTPError):
            def read(self, *_a, **_kw):
                raise OSError("body unreadable")

        err = _BadHTTPError("u", 500, "kaput", {}, None)
        self.addCleanup(err.close)
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch("ai_jury.adapters._open", side_effect=err):
            r = a.run("p")
        self.assertFalse(r.ok)
        self.assertIn("HTTP 500", r.error)
        # reason ("kaput") used since the body read failed.
        self.assertIn("kaput", r.error)
        self.assertEqual(r.error_code, adapters.ERR_NONZERO_EXIT)

    def test_run_http_error_detail_read_succeeds(self):
        # Complementary: exc.read() works -> detail body used.
        err = urllib.error.HTTPError(
            "u", 401, "unauth", {}, _Resp("auth error body")
        )
        self.addCleanup(err.close)
        a = adapters.LocalAdapter(_local_spec())
        with mock.patch("ai_jury.adapters._open", side_effect=err):
            r = a.run("p")
        self.assertFalse(r.ok)
        self.assertEqual(r.error_code, adapters.ERR_AUTH_REQUIRED)
        self.assertIn("auth error body", r.error)


class CacheCoverageTests(unittest.TestCase):
    def test_agent_result_none(self):
        # Line 122: a None entry round-trips to None (e.g. absent synthesis).
        self.assertIsNone(cache_mod._agent_result(None))

    def test_agent_result_present(self):
        ar = cache_mod._agent_result(
            {
                "agent": "a",
                "vendor": "v",
                "ok": True,
                "output": "o",
                "duration_s": 1.5,
            }
        )
        self.assertIsNotNone(ar)
        self.assertEqual(ar.agent, "a")
        self.assertEqual(ar.attempts, 1)

    def test_hit_builds_injection_hit(self):
        # Line 150: _hit constructs an InjectionHit from a dict.
        hit = cache_mod._hit(
            {"kind": "k", "source": "s", "line": 7, "snippet": "snip"}
        )
        self.assertEqual(hit.kind, "k")
        self.assertEqual(hit.source, "s")
        self.assertEqual(hit.line, 7)
        self.assertEqual(hit.snippet, "snip")

    def test_hit_defaults(self):
        hit = cache_mod._hit({})
        self.assertEqual(hit.kind, "")
        self.assertIsNone(hit.line)

    def test_load_schema_mismatch_is_miss(self):
        # Line 210: an entry with the wrong cache_schema is treated as a miss.
        with tempfile.TemporaryDirectory() as d:
            c = cache_mod.Cache(d)
            key = "abc"
            c._path(key).write_text(
                json.dumps({"cache_schema": cache_mod.CACHE_SCHEMA + 999, "outcome": {}}),
                encoding="utf-8",
            )
            self.assertIsNone(c.load(key))

    def test_load_missing_file_is_miss(self):
        with tempfile.TemporaryDirectory() as d:
            c = cache_mod.Cache(d)
            self.assertIsNone(c.load("does-not-exist"))

    def test_load_corrupt_json_is_miss(self):
        with tempfile.TemporaryDirectory() as d:
            c = cache_mod.Cache(d)
            key = "bad"
            c._path(key).write_text("{ not valid json", encoding="utf-8")
            self.assertIsNone(c.load(key))

    def test_clear_missing_dir_returns_zero(self):
        # Line 227: clearing a nonexistent cache dir returns 0, no error.
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "never-created"
            c = cache_mod.Cache(missing)
            self.assertFalse(missing.exists())
            self.assertEqual(c.clear(), 0)

    def test_clear_removes_entries(self):
        with tempfile.TemporaryDirectory() as d:
            c = cache_mod.Cache(d)
            c._path("k1").write_text("{}", encoding="utf-8")
            c._path("k2").write_text("{}", encoding="utf-8")
            self.assertEqual(c.clear(), 2)
            self.assertEqual(c.clear(), 0)

    def test_default_cache_dir_env_override(self):
        with mock.patch.dict("os.environ", {cache_mod._ENV_DIR: "/tmp/jc"}, clear=False):
            self.assertEqual(cache_mod.default_cache_dir(), Path("/tmp/jc"))

    def test_default_cache_dir_xdg(self):
        env = {"XDG_CACHE_HOME": "/tmp/xdg"}
        with mock.patch.dict("os.environ", env, clear=True):
            self.assertEqual(
                cache_mod.default_cache_dir(), Path("/tmp/xdg") / "ai-jury"
            )

    def test_default_cache_dir_home_fallback(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch("pathlib.Path.home", return_value=Path("/home/u")):
            self.assertEqual(
                cache_mod.default_cache_dir(),
                Path("/home/u") / ".cache" / "ai-jury",
            )


if __name__ == "__main__":
    unittest.main()
