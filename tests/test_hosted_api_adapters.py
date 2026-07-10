"""Tests for the hosted-vendor-API adapters (issue #430).

Network-free: exercises the pure helpers (payload/headers/parse/error mapping)
and config/privilege/scaffold wiring for ``anthropic-api``/``openai-api``. A
live smoke test would need a real API key and is intentionally out of scope
for the offline suite (mirrors the native-CLI live-smoke pattern).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury.adapters import (  # noqa: E402
    ERR_AUTH_REQUIRED,
    ERR_CONNECTION,
    ERR_INVALID_API_KEY,
    ERR_MISSING_API_KEY,
    ERR_NONZERO_EXIT,
    ERR_RATE_LIMITED,
    RETRYABLE_ERROR_CODES,
    AnthropicApiAdapter,
    GoogleApiAdapter,
    OpenAiApiAdapter,
    make_adapter,
)
from ai_jury.config import (  # noqa: E402
    AgentSpec,
    ConfigError,
    _from_dict,
    validate_config,
)
from ai_jury.privilege import audit_agent, enforce_read_only  # noqa: E402
from ai_jury.scaffold import KNOWN_AGENTS, agent_templates  # noqa: E402


def _anthropic_spec(**kw):
    base = {"name": "claude-api", "vendor": "anthropic-api", "model": "claude-x"}
    base.update(kw)
    return AgentSpec(**base)


def _openai_spec(**kw):
    base = {"name": "codex-api", "vendor": "openai-api", "model": "gpt-x"}
    base.update(kw)
    return AgentSpec(**base)


def _google_spec(**kw):
    base = {"name": "gemini-api", "vendor": "google-api", "model": "gemini-x"}
    base.update(kw)
    return AgentSpec(**base)


class PayloadAndHeadersTest(unittest.TestCase):
    def test_anthropic_payload_shape(self):
        a = AnthropicApiAdapter(_anthropic_spec())
        p = a.build_payload("review this")
        self.assertEqual(p["model"], "claude-x")
        self.assertEqual(p["messages"], [{"role": "user", "content": "review this"}])
        self.assertIn("max_tokens", p)
        self.assertGreater(p["max_tokens"], 0)

    def test_openai_payload_shape(self):
        a = OpenAiApiAdapter(_openai_spec())
        p = a.build_payload("review this")
        self.assertEqual(p["model"], "gpt-x")
        self.assertEqual(p["messages"], [{"role": "user", "content": "review this"}])
        self.assertNotIn("max_tokens", p)

    def test_anthropic_headers_carry_api_key_and_version(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-secret"}, clear=False):
            a = AnthropicApiAdapter(_anthropic_spec())
            headers = a._headers()
        self.assertEqual(headers["x-api-key"], "sk-ant-secret")
        self.assertIn("anthropic-version", headers)
        self.assertEqual(headers["Content-Type"], "application/json")

    def test_openai_headers_carry_bearer_token(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "sk-oai-secret"}, clear=False):
            a = OpenAiApiAdapter(_openai_spec())
            headers = a._headers()
        self.assertEqual(headers["Authorization"], "Bearer sk-oai-secret")

    def test_google_payload_shape(self):
        a = GoogleApiAdapter(_google_spec())
        p = a.build_payload("review this")
        self.assertEqual(p["contents"], [{"parts": [{"text": "review this"}]}])
        self.assertNotIn("model", p)

    def test_google_headers_carry_api_key(self):
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "sk-goog-secret"}, clear=False):
            a = GoogleApiAdapter(_google_spec())
            headers = a._headers()
        self.assertEqual(headers["x-goog-api-key"], "sk-goog-secret")
        self.assertEqual(headers["Content-Type"], "application/json")
        # The key must never appear in the URL — only the header form is used.
        self.assertNotIn("sk-goog-secret", a._api_url())

    def test_google_url_embeds_model_in_path(self):
        a = GoogleApiAdapter(_google_spec(model="gemini-2.5-pro"))
        self.assertEqual(
            a._api_url(),
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-2.5-pro:generateContent",
        )

    def test_google_url_escapes_reserved_characters_in_model(self):
        # A model value containing reserved URL characters must not change
        # the request's path/query semantics — it stays a single escaped
        # path segment (caught in review: unescaped interpolation would let
        # e.g. a `/` or `?` in `model` smuggle an extra path/query).
        a = GoogleApiAdapter(_google_spec(model="weird/model?x=1#frag"))
        url = a._api_url()
        self.assertNotIn("?x=1", url)
        self.assertTrue(url.startswith("https://generativelanguage.googleapis.com/v1beta/models/"))
        # Exactly one path segment between "models/" and ":generateContent".
        segment = url.split("/models/", 1)[1].rsplit(":generateContent", 1)[0]
        self.assertNotIn("/", segment)


class ParseAndErrorMappingTest(unittest.TestCase):
    def test_anthropic_parse_content(self):
        data = {"content": [{"type": "text", "text": "  a finding  "}]}
        self.assertEqual(AnthropicApiAdapter.parse_content(data), "a finding")

    def test_anthropic_parse_concatenates_multiple_text_blocks(self):
        data = {
            "content": [
                {"type": "text", "text": "part one. "},
                {"type": "text", "text": "part two."},
            ]
        }
        self.assertEqual(AnthropicApiAdapter.parse_content(data), "part one. part two.")

    def test_anthropic_parse_ignores_non_text_blocks(self):
        data = {"content": [{"type": "tool_use", "id": "x"}, {"type": "text", "text": "ok"}]}
        self.assertEqual(AnthropicApiAdapter.parse_content(data), "ok")

    def test_anthropic_parse_empty_content(self):
        self.assertEqual(AnthropicApiAdapter.parse_content({"content": []}), "")

    def test_openai_parse_content(self):
        data = {"choices": [{"message": {"content": "  a finding  "}}]}
        self.assertEqual(OpenAiApiAdapter.parse_content(data), "a finding")

    def test_openai_parse_empty_choices(self):
        self.assertEqual(OpenAiApiAdapter.parse_content({"choices": []}), "")

    def test_google_parse_content(self):
        data = {"candidates": [{"content": {"parts": [{"text": "  a finding  "}]}}]}
        self.assertEqual(GoogleApiAdapter.parse_content(data), "a finding")

    def test_google_parse_concatenates_multiple_parts(self):
        data = {
            "candidates": [
                {"content": {"parts": [{"text": "part one. "}, {"text": "part two."}]}}
            ]
        }
        self.assertEqual(GoogleApiAdapter.parse_content(data), "part one. part two.")

    def test_google_parse_empty_candidates(self):
        self.assertEqual(GoogleApiAdapter.parse_content({"candidates": []}), "")

    def test_google_parse_blocked_prompt_has_no_content(self):
        # A safety-filter block yields no `content` key on the candidate.
        data = {"candidates": [{"finishReason": "SAFETY"}], "promptFeedback": {"blockReason": "SAFETY"}}
        self.assertEqual(GoogleApiAdapter.parse_content(data), "")

    def test_connection_error_is_retryable(self):
        self.assertIn(ERR_CONNECTION, RETRYABLE_ERROR_CODES)

    def test_missing_api_key_is_not_retryable(self):
        # A permanently-unset key is a config problem, not a transient failure.
        self.assertNotIn(ERR_MISSING_API_KEY, RETRYABLE_ERROR_CODES)

    def test_invalid_api_key_is_not_retryable(self):
        self.assertNotIn(ERR_INVALID_API_KEY, RETRYABLE_ERROR_CODES)


class AvailabilityTest(unittest.TestCase):
    def test_unavailable_without_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(AnthropicApiAdapter(_anthropic_spec()).available())
            self.assertFalse(OpenAiApiAdapter(_openai_spec()).available())
            self.assertFalse(GoogleApiAdapter(_google_spec()).available())

    def test_available_with_key(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False):
            self.assertTrue(AnthropicApiAdapter(_anthropic_spec()).available())
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "x"}, clear=False):
            self.assertTrue(OpenAiApiAdapter(_openai_spec()).available())
        with mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}, clear=False):
            self.assertTrue(GoogleApiAdapter(_google_spec()).available())

    def test_detect_capabilities_reports_missing_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            caps = AnthropicApiAdapter(_anthropic_spec()).detect_capabilities()
        self.assertEqual(caps["status"], "unavailable")
        self.assertTrue(caps["warnings"])

    def test_detect_capabilities_ok_with_key(self):
        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "x"}, clear=False):
            caps = OpenAiApiAdapter(_openai_spec()).detect_capabilities()
        self.assertEqual(caps["status"], "ok")
        self.assertEqual(caps["warnings"], [])

    def test_available_and_capabilities_false_for_invalid_key(self):
        # A key that IS set but contains a control character must not report
        # as available/CAP_OK — run() will reject it, so a capability check
        # (jury --doctor) should agree, not give a falsely reassuring answer.
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "bad\nkey"}, clear=False):
            adapter = AnthropicApiAdapter(_anthropic_spec())
            self.assertFalse(adapter.available())
            caps = adapter.detect_capabilities()
        self.assertEqual(caps["status"], "unavailable")
        self.assertTrue(caps["warnings"])
        self.assertNotIn("bad\nkey", caps["warnings"][0])


class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *_args):
        return self._payload


class RunHttpTest(unittest.TestCase):
    """Exercise run()'s HTTP success/error branches offline by patching urllib."""

    def test_missing_key_short_circuits_before_any_request(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("ai_jury.adapters._open") as opened,
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        opened.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_MISSING_API_KEY)

    def test_anthropic_success(self):
        import json

        payload = json.dumps({"content": [{"type": "text", "text": "a real finding"}]}).encode()
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "a real finding")

    def test_openai_success(self):
        import json

        payload = json.dumps({"choices": [{"message": {"content": "a real finding"}}]}).encode()
        with (
            mock.patch.dict("os.environ", {"OPENAI_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)),
        ):
            result = OpenAiApiAdapter(_openai_spec()).run("prompt")
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "a real finding")

    def test_google_success(self):
        import json

        payload = json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "a real finding"}]}}]}
        ).encode()
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)) as opened,
        ):
            result = GoogleApiAdapter(_google_spec()).run("prompt")
        self.assertTrue(result.ok)
        self.assertEqual(result.output, "a real finding")
        # The request hit the model-specific URL, not a fixed constant.
        self.assertIn("gemini-x:generateContent", opened.call_args.args[0].full_url)

    def test_google_missing_key_short_circuits_before_any_request(self):
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("ai_jury.adapters._open") as opened,
        ):
            result = GoogleApiAdapter(_google_spec()).run("prompt")
        opened.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_MISSING_API_KEY)

    def test_google_key_with_embedded_newline_is_rejected_before_any_request(self):
        secret = "totally-real-secret-value-987\nX-Injected: evil"
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": secret}, clear=False),
            mock.patch("ai_jury.adapters._open") as opened,
        ):
            result = GoogleApiAdapter(_google_spec()).run("prompt")
        opened.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_INVALID_API_KEY)
        self.assertNotIn(secret, result.error)

    def test_google_http_error_maps_to_rate_limit(self):
        import io
        import urllib.error

        err = urllib.error.HTTPError(
            url="x", code=429, msg="Too Many Requests", hdrs=None, fp=io.BytesIO(b"slow down")
        )
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=err),
        ):
            result = GoogleApiAdapter(_google_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_RATE_LIMITED)

    def test_google_empty_content_is_failure(self):
        import json

        payload = json.dumps({"candidates": []}).encode()
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)),
        ):
            result = GoogleApiAdapter(_google_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "empty_output")

    def test_empty_content_is_failure(self):
        import json

        payload = json.dumps({"content": [{"type": "text", "text": "   "}]}).encode()
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "empty_output")

    def test_connection_error(self):
        import urllib.error

        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=urllib.error.URLError("refused")),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_CONNECTION)

    def test_http_error_maps_to_auth_required(self):
        import io
        import urllib.error

        err = urllib.error.HTTPError(
            url="x", code=401, msg="Unauthorized", hdrs=None, fp=io.BytesIO(b"bad key")
        )
        with (
            mock.patch.dict("os.environ", {"OPENAI_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=err),
        ):
            result = OpenAiApiAdapter(_openai_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_AUTH_REQUIRED)

    def test_http_error_maps_to_rate_limit(self):
        import io
        import urllib.error

        err = urllib.error.HTTPError(
            url="x", code=429, msg="Too Many Requests", hdrs=None, fp=io.BytesIO(b"slow down")
        )
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=err),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_RATE_LIMITED)

    def test_http_error_other_status_is_nonzero_exit(self):
        import io
        import urllib.error

        err = urllib.error.HTTPError(
            url="x", code=500, msg="Server Error", hdrs=None, fp=io.BytesIO(b"oops")
        )
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=err),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_NONZERO_EXIT)

    def test_timeout_is_typed(self):
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=TimeoutError),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "timeout")

    def test_run_timeout_override_is_capped_by_agent_timeout(self):
        # Exercises the `timeout is not None` override branch in run(); the
        # actual effective value only affects the mocked _open call's timeout
        # arg, so just assert the run still completes normally.
        import json

        payload = json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode()
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)) as opened,
        ):
            result = AnthropicApiAdapter(_anthropic_spec(timeout=600)).run("prompt", timeout=5)
        self.assertTrue(result.ok)
        self.assertEqual(opened.call_args.args[1], 5)

    def test_http_error_body_read_failure_falls_back_to_reason(self):
        import urllib.error

        class _UnreadableHTTPError(urllib.error.HTTPError):
            def read(self, *_args):
                raise OSError("body already consumed")

        err = _UnreadableHTTPError(
            url="x", code=500, msg="Server Error", hdrs=None, fp=None
        )
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=err),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertIn("Server Error", result.error)

    def test_unexpected_exception_is_unknown(self):
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", side_effect=RuntimeError("boom")),
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "unknown")

    def test_key_with_embedded_newline_is_rejected_before_any_request(self):
        # A key value containing a control character (a plausible artifact of
        # a secret loaded from a file/k8s mount with a trailing newline) would
        # trip CPython's http.client header-injection guard. That guard
        # reports the rejected value via repr() — which escapes the newline
        # to the two literal characters `\` `n`, no longer byte-for-byte equal
        # to the raw key — so a literal-substring scrub of the exception text
        # CANNOT reliably catch it (see test_repr_escaping_defeats_literal_
        # scrub_alone below for a direct demonstration). The key must never
        # reach a header in the first place: _invalid_key_reason() rejects it
        # up front, so _open is never even called.
        secret = "totally-real-secret-value-987\nX-Injected: evil"
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": secret}, clear=False),
            mock.patch("ai_jury.adapters._open") as opened,
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        opened.assert_not_called()
        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, ERR_INVALID_API_KEY)
        self.assertNotIn(secret, result.error)
        self.assertNotIn("\n", result.error)

    def test_clean_key_is_not_rejected(self):
        import json

        payload = json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode()
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-perfectly-normal"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)) as opened,
        ):
            result = AnthropicApiAdapter(_anthropic_spec()).run("prompt")
        opened.assert_called_once()
        self.assertTrue(result.ok)

    def test_invalid_key_reason_flags_control_characters(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "bad\nkey"}, clear=False):
            adapter = AnthropicApiAdapter(_anthropic_spec())
            self.assertIsNotNone(adapter._invalid_key_reason())
            self.assertNotIn("bad\nkey", adapter._invalid_key_reason())

    def test_invalid_key_reason_none_for_clean_key(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-clean"}, clear=False):
            adapter = AnthropicApiAdapter(_anthropic_spec())
            self.assertIsNone(adapter._invalid_key_reason())

    def test_repr_escaping_defeats_literal_scrub_alone(self):
        # Documents *why* _invalid_key_reason exists rather than relying on
        # _scrub_secret alone: repr() of a string containing a real newline
        # byte produces the two literal characters `\` `n`, which is not a
        # substring match against the original (unescaped) key — so a naive
        # literal scrub silently fails to redact it. This is exactly the gap
        # an earlier version of this fix had (caught in review).
        secret = "totally-real-secret-value-987\nEvil: header"
        http_client_style_message = f"Invalid header value {secret!r}"
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": secret}, clear=False):
            adapter = AnthropicApiAdapter(_anthropic_spec())
            scrubbed = adapter._scrub_secret(http_client_style_message)
        # The raw secret is gone (repr already escaped it) but so is any
        # "[REDACTED]" marker — _scrub_secret is a no-op here, proving it
        # alone would NOT have been a sufficient fix for this case.
        self.assertNotIn(secret, scrubbed)
        self.assertNotIn("[REDACTED]", scrubbed)

    def test_scrub_secret_still_catches_a_verbatim_echo(self):
        # A single-line key (no control characters — already ruled out by
        # _invalid_key_reason before any request is made) could still turn up
        # verbatim in some unrelated error text, e.g. a misbehaving endpoint
        # echoing a header back in a JSON error body. _scrub_secret remains a
        # useful second layer for exactly that case.
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-plainkey"}, clear=False):
            adapter = AnthropicApiAdapter(_anthropic_spec())
            scrubbed = adapter._scrub_secret("server echoed header: sk-ant-plainkey")
        self.assertNotIn("sk-ant-plainkey", scrubbed)
        self.assertIn("[REDACTED]", scrubbed)

    def test_scrub_secret_is_a_no_op_when_key_absent_from_text(self):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-x"}, clear=False):
            adapter = AnthropicApiAdapter(_anthropic_spec())
            self.assertEqual(adapter._scrub_secret("unrelated error text"), "unrelated error text")


class ParseContentMalformedShapeTest(unittest.TestCase):
    """A malformed/unexpected JSON response body (not a dict) must not crash
    parse_content — it should degrade to empty content, which run() already
    turns into a clean ERR_EMPTY_OUTPUT failure."""

    def test_anthropic_parse_content_non_dict_body(self):
        self.assertEqual(AnthropicApiAdapter.parse_content([1, 2, 3]), "")
        self.assertEqual(AnthropicApiAdapter.parse_content("not a dict"), "")
        self.assertEqual(AnthropicApiAdapter.parse_content(None), "")

    def test_openai_parse_content_non_dict_body(self):
        self.assertEqual(OpenAiApiAdapter.parse_content([1, 2, 3]), "")
        self.assertEqual(OpenAiApiAdapter.parse_content("not a dict"), "")
        self.assertEqual(OpenAiApiAdapter.parse_content(None), "")

    def test_google_parse_content_non_dict_body(self):
        self.assertEqual(GoogleApiAdapter.parse_content([1, 2, 3]), "")
        self.assertEqual(GoogleApiAdapter.parse_content("not a dict"), "")
        self.assertEqual(GoogleApiAdapter.parse_content(None), "")
        # Malformed nested shapes (non-dict candidate / content) must also
        # degrade cleanly rather than raising.
        self.assertEqual(GoogleApiAdapter.parse_content({"candidates": ["not-a-dict"]}), "")
        self.assertEqual(
            GoogleApiAdapter.parse_content({"candidates": [{"content": "not-a-dict"}]}), ""
        )

    def test_google_parse_content_non_string_text_does_not_raise(self):
        # A malformed `text` value (e.g. an int) must not crash "".join() —
        # caught in review: the part contributes nothing instead of raising.
        data = {"candidates": [{"content": {"parts": [{"text": 1}, {"text": "ok"}]}}]}
        self.assertEqual(GoogleApiAdapter.parse_content(data), "ok")


class FactoryAndConfigTest(unittest.TestCase):
    def test_make_adapter_returns_anthropic_api(self):
        self.assertIsInstance(make_adapter(_anthropic_spec()), AnthropicApiAdapter)

    def test_make_adapter_returns_openai_api(self):
        self.assertIsInstance(make_adapter(_openai_spec()), OpenAiApiAdapter)

    def test_make_adapter_returns_google_api(self):
        self.assertIsInstance(make_adapter(_google_spec()), GoogleApiAdapter)

    def test_hosted_api_agent_valid_without_command(self):
        cfg = {
            "jury": {"chair": "claude-api"},
            "agent": [{"name": "claude-api", "vendor": "anthropic-api", "model": "claude-x"}],
        }
        warnings = validate_config(cfg)
        self.assertFalse(any("missing a non-empty 'command'" in w for w in warnings))
        spec = _from_dict(cfg).agents[0]
        self.assertEqual(spec.command, "")

    def test_hosted_api_without_model_warns(self):
        cfg = {"jury": {}, "agent": [{"name": "codex-api", "vendor": "openai-api"}]}
        warnings = validate_config(cfg)
        self.assertTrue(any("has no 'model'" in w for w in warnings))

    def test_hosted_api_vendors_are_known(self):
        cfg = {
            "jury": {},
            "agent": [
                {"name": "a", "vendor": "anthropic-api", "model": "m"},
                {"name": "b", "vendor": "openai-api", "model": "m"},
                {"name": "c", "vendor": "google-api", "model": "m"},
            ],
        }
        warnings = validate_config(cfg)
        self.assertFalse(any("unknown vendor" in w for w in warnings))

    def test_google_api_agent_valid_without_command(self):
        cfg = {
            "jury": {"chair": "gemini-api"},
            "agent": [{"name": "gemini-api", "vendor": "google-api", "model": "gemini-x"}],
        }
        warnings = validate_config(cfg)
        self.assertFalse(any("missing a non-empty 'command'" in w for w in warnings))
        spec = _from_dict(cfg).agents[0]
        self.assertEqual(spec.command, "")

    def test_hosted_api_agent_does_not_get_endpoint_validated(self):
        # No `endpoint` key at all is fine — the URL is fixed, not a config value.
        cfg = {"jury": {}, "agent": [{"name": "a", "vendor": "anthropic-api", "model": "m"}]}
        # Must not raise even though there's no `endpoint`/SSRF check to run.
        validate_config(cfg)

    def test_native_anthropic_vendor_still_requires_command(self):
        # "anthropic" (native CLI) and "anthropic-api" (hosted) are distinct
        # vendors; the CLI one is unaffected by the new no-command carve-out.
        cfg = {"jury": {}, "agent": [{"name": "x", "vendor": "anthropic"}]}
        with self.assertRaises(ConfigError):
            validate_config(cfg)


class PrivilegeAuditTest(unittest.TestCase):
    def test_hosted_api_agent_has_no_privilege_warnings(self):
        spec = _anthropic_spec()
        self.assertEqual(audit_agent(spec), [])
        spec2 = _openai_spec()
        self.assertEqual(audit_agent(spec2), [])
        spec3 = _google_spec()
        self.assertEqual(audit_agent(spec3), [])

    def test_enforce_read_only_is_a_no_op_for_hosted_api(self):
        self.assertEqual(enforce_read_only("anthropic-api", "claude-api", []), [])
        self.assertEqual(enforce_read_only("openai-api", "codex-api", ["whatever"]), ["whatever"])
        self.assertEqual(enforce_read_only("google-api", "gemini-api", []), [])


class ScaffoldTemplateTest(unittest.TestCase):
    def test_hosted_api_templates_present(self):
        templates = agent_templates()
        self.assertIn("claude-api", templates)
        self.assertIn("codex-api", templates)
        self.assertIn("gemini-api", templates)
        self.assertEqual(templates["claude-api"]["vendor"], "anthropic-api")
        self.assertEqual(templates["codex-api"]["vendor"], "openai-api")
        self.assertEqual(templates["gemini-api"]["vendor"], "google-api")
        self.assertNotIn("command", templates["claude-api"])
        self.assertNotIn("command", templates["codex-api"])
        self.assertNotIn("command", templates["gemini-api"])

    def test_hosted_api_names_in_known_agents(self):
        self.assertIn("claude-api", KNOWN_AGENTS)
        self.assertIn("codex-api", KNOWN_AGENTS)
        self.assertIn("gemini-api", KNOWN_AGENTS)


if __name__ == "__main__":
    unittest.main()
