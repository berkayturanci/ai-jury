"""Tests for the local / open-weight OpenAI-compatible adapter (issue #43).

Network-free: exercises the pure helpers (URL/payload/parse/error mapping) and
config wiring. A live Ollama smoke test lives in tests/live/ (opt-in).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.adapters import (  # noqa: E402
    ERR_AUTH_REQUIRED,
    ERR_CONNECTION,
    ERR_NONZERO_EXIT,
    ERR_RATE_LIMITED,
    RETRYABLE_ERROR_CODES,
    LocalAdapter,
    make_adapter,
)
from ai_jury.config import (  # noqa: E402
    AgentSpec,
    ConfigError,
    _from_dict,
    validate_config,
)


def _spec(**kw):
    base = {"name": "local", "vendor": "local", "model": "qwen2.5-coder:7b"}
    base.update(kw)
    return AgentSpec(**base)


class UrlAndPayloadTest(unittest.TestCase):
    def test_default_endpoint(self):
        a = LocalAdapter(_spec())
        self.assertEqual(a.completions_url(), "http://localhost:11434/v1/chat/completions")

    def test_base_url_gets_chat_completions_appended(self):
        a = LocalAdapter(_spec(endpoint="http://host:8000/v1/"))
        self.assertEqual(a.completions_url(), "http://host:8000/v1/chat/completions")

    def test_full_completions_url_left_as_is(self):
        a = LocalAdapter(_spec(endpoint="http://host:8000/v1/chat/completions"))
        self.assertEqual(a.completions_url(), "http://host:8000/v1/chat/completions")

    def test_payload_shape(self):
        a = LocalAdapter(_spec())
        p = a.build_payload("review this")
        self.assertEqual(p["model"], "qwen2.5-coder:7b")
        self.assertEqual(p["messages"], [{"role": "user", "content": "review this"}])
        self.assertFalse(p["stream"])


class ParseAndErrorTest(unittest.TestCase):
    def test_parse_content(self):
        data = {"choices": [{"message": {"content": "  hello  "}}]}
        self.assertEqual(LocalAdapter.parse_content(data), "hello")

    def test_parse_empty_choices(self):
        self.assertEqual(LocalAdapter.parse_content({"choices": []}), "")

    def test_http_status_mapping(self):
        self.assertEqual(LocalAdapter.classify_http_status(401), ERR_AUTH_REQUIRED)
        self.assertEqual(LocalAdapter.classify_http_status(403), ERR_AUTH_REQUIRED)
        self.assertEqual(LocalAdapter.classify_http_status(429), ERR_RATE_LIMITED)
        self.assertEqual(LocalAdapter.classify_http_status(500), ERR_NONZERO_EXIT)

    def test_connection_error_is_retryable(self):
        self.assertIn(ERR_CONNECTION, RETRYABLE_ERROR_CODES)


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *args):
        return self._payload


class RunHttpTest(unittest.TestCase):
    """Exercise run()'s HTTP success/error branches offline by patching urllib."""

    def test_success(self):
        import json
        from unittest import mock

        payload = json.dumps(
            {"choices": [{"message": {"content": "a review with a finding"}}]}
        ).encode()
        with mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)):
            result = LocalAdapter(_spec()).run("prompt")
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "a review with a finding")

    def test_empty_content_is_failure(self):
        import json
        from unittest import mock

        payload = json.dumps({"choices": [{"message": {"content": "   "}}]}).encode()
        with mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)):
            result = LocalAdapter(_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "empty_output")

    def test_connection_error(self):
        import urllib.error
        from unittest import mock

        with mock.patch(
            "ai_jury.adapters._open", side_effect=urllib.error.URLError("refused")
        ):
            result = LocalAdapter(_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_CONNECTION)

    def test_http_error_maps_to_rate_limit(self):
        import io
        import urllib.error
        from unittest import mock

        err = urllib.error.HTTPError(
            url="x", code=429, msg="Too Many Requests", hdrs=None, fp=io.BytesIO(b"slow down")
        )
        with mock.patch("ai_jury.adapters._open", side_effect=err):
            result = LocalAdapter(_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_RATE_LIMITED)


class FactoryAndConfigTest(unittest.TestCase):
    def test_make_adapter_returns_local(self):
        self.assertIsInstance(make_adapter(_spec()), LocalAdapter)

    def test_local_agent_valid_without_command(self):
        cfg = {
            "jury": {"chair": "local"},
            "agent": [
                {"name": "local", "vendor": "local", "model": "qwen2.5-coder:7b",
                 "endpoint": "http://localhost:11434/v1"}
            ],
        }
        # No hard errors (missing command is allowed for local).
        warnings = validate_config(cfg)
        self.assertFalse(any("missing a non-empty 'command'" in w for w in warnings))
        spec = _from_dict(cfg).agents[0]
        self.assertEqual(spec.endpoint, "http://localhost:11434/v1")
        self.assertEqual(spec.command, "")

    def test_local_without_model_warns(self):
        cfg = {
            "jury": {},
            "agent": [{"name": "local", "vendor": "local"}],
        }
        warnings = validate_config(cfg)
        self.assertTrue(any("has no 'model'" in w for w in warnings))

    def test_non_local_still_requires_command(self):
        cfg = {"jury": {}, "agent": [{"name": "x", "vendor": "anthropic"}]}
        with self.assertRaises(ConfigError):
            validate_config(cfg)

    def test_local_is_a_known_vendor(self):
        cfg = {"jury": {}, "agent": [
            {"name": "local", "vendor": "local", "model": "m"}]}
        warnings = validate_config(cfg)
        self.assertFalse(any("unknown vendor" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
