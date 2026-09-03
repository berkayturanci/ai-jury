"""Offline tests for adapter argv/stdin construction.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live CLIs, no network — the real ``codex``
binary is never invoked; we only inspect what ``build_argv`` / ``_stdin_for``
would produce.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import json  # noqa: E402
import unittest.mock as mock  # noqa: E402

from ai_jury.adapters import (  # noqa: E402
    EFFORT_LEVELS,
    AgyAdapter,
    AnthropicApiAdapter,
    ClaudeAdapter,
    CodexAdapter,
    EffortPlan,
    GenericOpenAICompatibleAdapter,
    GoogleApiAdapter,
    LocalAdapter,
    OpenAiApiAdapter,
    effort_args,
    effort_supported,
    effort_warnings,
    parse_model_list,
)
from ai_jury.config import KNOWN_EFFORTS, AgentSpec  # noqa: E402

PROMPT = "Review this diff and report findings."


def _codex_spec(model: str | None = None, extra_args: list[str] | None = None) -> AgentSpec:
    return AgentSpec(
        name="codex",
        vendor="openai",
        command="codex",
        model=model,
        extra_args=list(extra_args if extra_args is not None else ["-s", "danger-full-access"]),
    )


class CodexAdapterTest(unittest.TestCase):
    def test_build_argv_does_not_contain_prompt(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertNotIn(PROMPT, argv)

    def test_stdin_for_returns_prompt(self):
        adapter = CodexAdapter(_codex_spec())
        self.assertEqual(adapter._stdin_for(PROMPT), PROMPT)

    def test_argv_starts_with_command_and_exec(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertEqual(argv[0], "codex")
        self.assertIn("exec", argv)

    def test_argv_includes_configured_extra_args(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertIn("-s", argv)
        self.assertIn("danger-full-access", argv)

    def test_custom_extra_args_are_passed_through(self):
        # Custom args pass through; the secure-default sandbox (-s read-only) is
        # injected because none was configured (issue #288 enforcement).
        argv = CodexAdapter(_codex_spec(extra_args=["--foo", "bar"])).build_argv(PROMPT)
        self.assertEqual(argv, ["codex", "exec", "-s", "read-only", "--foo", "bar"])

    def test_model_flag_present_when_model_set(self):
        argv = CodexAdapter(_codex_spec(model="gpt-5-codex")).build_argv(PROMPT)
        self.assertIn("-m", argv)
        self.assertEqual(argv[argv.index("-m") + 1], "gpt-5-codex")

    def test_model_flag_absent_when_model_unset(self):
        argv = CodexAdapter(_codex_spec()).build_argv(PROMPT)
        self.assertNotIn("-m", argv)


# --------------------------------------------------------------------------
# Reasoning effort (issue #662)
# --------------------------------------------------------------------------


class _FakeResp:
    """Minimal stand-in for the object ``adapters._open`` yields."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self, *_args):
        return self._payload


def _spec(vendor, **kw) -> AgentSpec:
    return AgentSpec(name=kw.pop("name", vendor), vendor=vendor, **kw)


class EffortMappingTest(unittest.TestCase):
    """``effort_args`` is the single place a vendor's effort knob is decided."""

    def test_levels_agree_with_config_validation(self):
        # The two lists are deliberately separate (config must not import
        # adapters); this pins them together so they cannot drift.
        self.assertEqual(tuple(EFFORT_LEVELS), tuple(KNOWN_EFFORTS))

    def test_no_effort_is_a_no_op_plan(self):
        for value in (None, "", "   "):
            plan = effort_args("anthropic-api", value)
            self.assertEqual(plan, EffortPlan())
            self.assertIsNone(plan.warning)

    def test_unknown_level_raises(self):
        with self.assertRaises(ValueError) as ctx:
            effort_args("openai-api", "maximum")
        self.assertIn("low, medium, high", str(ctx.exception))

    def test_agy_appends_the_level_as_a_model_suffix(self):
        plan = effort_args("google", "high", "gemini-3.8-flash")
        self.assertEqual(plan.model, "gemini-3.8-flash-high")
        self.assertEqual(plan.payload, {})
        self.assertIsNone(plan.warning)

    def test_agy_leaves_an_already_suffixed_model_alone(self):
        for suffix in EFFORT_LEVELS:
            model = f"gemini-3.8-flash-{suffix}"
            self.assertEqual(effort_args("google", "high", model).model, model)

    def test_agy_without_a_model_warns_instead_of_guessing(self):
        plan = effort_args("google", "low", None)
        self.assertIsNone(plan.model)
        self.assertIn("needs a configured model", plan.warning)

    def test_anthropic_api_thinking_budgets(self):
        budgets = {
            level: effort_args("anthropic-api", level).payload["thinking"]["budget_tokens"]
            for level in EFFORT_LEVELS
        }
        self.assertEqual(budgets, {"low": 2048, "medium": 8192, "high": 32768})
        self.assertEqual(effort_args("anthropic-api", "low").payload["thinking"]["type"], "enabled")

    def test_openai_shaped_apis_use_reasoning_effort(self):
        for vendor in ("openai-api", "openai-compatible"):
            for level in EFFORT_LEVELS:
                self.assertEqual(effort_args(vendor, level).payload, {"reasoning_effort": level})

    def test_google_api_thinking_config_budgets(self):
        budgets = {
            level: effort_args("google-api", level).payload["generationConfig"]["thinkingConfig"][
                "thinkingBudget"
            ]
            for level in EFFORT_LEVELS
        }
        self.assertEqual(budgets, {"low": 1024, "medium": 8192, "high": 32768})

    def test_cli_vendors_warn_once_and_are_ignored(self):
        for vendor in ("anthropic", "openai"):
            plan = effort_args(vendor, "high", "some-model")
            self.assertFalse(plan.supported)
            self.assertEqual(plan.payload, {})
            self.assertIsNone(plan.model)
            self.assertEqual(plan.warning, f"effort unsupported for {vendor}, ignored")

    def test_local_and_unknown_vendors_are_unsupported(self):
        # A local OpenAI-compatible server may reject an unknown request field,
        # so effort is refused rather than sent on speculation.
        for vendor in ("local", "cli", "made-up"):
            self.assertFalse(effort_args(vendor, "medium").supported)
            self.assertFalse(effort_supported(vendor))

    def test_effort_supported_matches_the_mapping(self):
        for vendor in ("google", "anthropic-api", "openai-api", "openai-compatible", "google-api"):
            self.assertTrue(effort_supported(vendor))
            self.assertTrue(effort_args(vendor, "medium", "m").supported)

    def test_vendor_and_level_are_case_and_space_insensitive(self):
        self.assertEqual(
            effort_args(" OpenAI-API ", " HIGH ").payload, {"reasoning_effort": "high"}
        )


class EffortWarningsTest(unittest.TestCase):
    def test_one_message_per_distinct_warning(self):
        agents = [
            _spec("anthropic", name="claude", command="claude", effort="high"),
            _spec("anthropic", name="claude2", command="claude", effort="high"),
            _spec("openai", name="codex", command="codex", effort="high"),
            _spec("openai-api", name="gpt", model="gpt-x", effort="high"),
        ]
        self.assertEqual(
            effort_warnings(agents),
            [
                "effort unsupported for anthropic, ignored",
                "effort unsupported for openai, ignored",
            ],
        )

    def test_no_effort_configured_produces_no_warnings(self):
        self.assertEqual(effort_warnings([_spec("anthropic", command="claude")]), [])

    def test_invalid_level_surfaces_as_a_warning_not_a_crash(self):
        warnings = effort_warnings([_spec("openai-api", model="gpt-x", effort="turbo")])
        self.assertEqual(len(warnings), 1)
        self.assertIn("unknown effort", warnings[0])


class EffortAppliedToRequestsTest(unittest.TestCase):
    """Each adapter actually sends what ``effort_args`` decided (mocked HTTP)."""

    @staticmethod
    def _sent_body(opened):
        return json.loads(opened.call_args.args[0].data.decode("utf-8"))

    def test_anthropic_run_sends_thinking_and_raises_max_tokens(self):
        payload = json.dumps({"content": [{"type": "text", "text": "ok"}]}).encode()
        spec = _spec("anthropic-api", model="claude-x", effort="high")
        with (
            mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)) as opened,
        ):
            result = AnthropicApiAdapter(spec).run("prompt")
        self.assertTrue(result.ok)
        body = self._sent_body(opened)
        self.assertEqual(body["thinking"], {"type": "enabled", "budget_tokens": 32768})
        # max_tokens must exceed the thinking budget or the API rejects the call.
        self.assertGreater(body["max_tokens"], body["thinking"]["budget_tokens"])

    def test_anthropic_without_effort_keeps_the_plain_body(self):
        spec = _spec("anthropic-api", model="claude-x")
        body = AnthropicApiAdapter(spec).build_payload("p")
        self.assertNotIn("thinking", body)
        self.assertEqual(body["max_tokens"], 4096)

    def test_openai_run_sends_reasoning_effort(self):
        payload = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        spec = _spec("openai-api", model="gpt-x", effort="low")
        with (
            mock.patch.dict("os.environ", {"OPENAI_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)) as opened,
        ):
            result = OpenAiApiAdapter(spec).run("prompt")
        self.assertTrue(result.ok)
        self.assertEqual(self._sent_body(opened)["reasoning_effort"], "low")

    def test_google_run_sends_thinking_config(self):
        payload = json.dumps({"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}).encode()
        spec = _spec("google-api", model="gemini-x", effort="medium")
        with (
            mock.patch.dict("os.environ", {"GEMINI_API_KEY": "x"}, clear=False),
            mock.patch("ai_jury.adapters._open", return_value=_FakeResp(payload)) as opened,
        ):
            result = GoogleApiAdapter(spec).run("prompt")
        self.assertTrue(result.ok)
        body = self._sent_body(opened)
        self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingBudget"], 8192)
        self.assertIn("contents", body)

    def test_openai_compatible_payload_carries_effort(self):
        spec = _spec("openai-compatible", model="deepseek-coder", effort="high")
        body = GenericOpenAICompatibleAdapter(spec).build_payload("p")
        self.assertEqual(body["reasoning_effort"], "high")

    def test_local_payload_is_untouched_by_effort(self):
        spec = _spec("local", model="qwen:7b", endpoint="http://localhost:11434/v1", effort="high")
        body = LocalAdapter(spec).build_payload("p")
        self.assertNotIn("reasoning_effort", body)

    def test_agy_argv_uses_the_effort_suffixed_model(self):
        spec = _spec("google", name="agy", command="agy", model="gemini-3.8-flash", effort="high")
        argv = AgyAdapter(spec).build_argv(PROMPT)
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash-high")

    def test_agy_argv_without_effort_is_unchanged(self):
        spec = _spec("google", name="agy", command="agy", model="gemini-3.8-flash")
        argv = AgyAdapter(spec).build_argv(PROMPT)
        self.assertEqual(argv[argv.index("--model") + 1], "gemini-3.8-flash")

    def test_claude_and_codex_argv_gain_no_effort_flag(self):
        claude = ClaudeAdapter(
            _spec("anthropic", name="claude", command="claude", model="m", effort="high")
        ).build_argv(PROMPT)
        codex = CodexAdapter(
            _spec("openai", name="codex", command="codex", model="m", effort="high")
        ).build_argv(PROMPT)
        for argv in (claude, codex):
            self.assertFalse(any("high" in arg for arg in argv), argv)

    def test_invalid_effort_degrades_to_a_no_op_plan(self):
        # validate_config rejects this; a run must not crash if it slips through.
        spec = _spec("openai-api", model="gpt-x", effort="turbo")
        self.assertEqual(OpenAiApiAdapter(spec).effort_plan(), EffortPlan())


class ParseModelListTest(unittest.TestCase):
    """``parse_model_list`` is pure and forgiving — a CLI's format is no contract."""

    def test_plain_lines_with_bullets(self):
        raw = "- gemini-3.8-flash\n* gemini-3.8-pro\n\ngemini-3.8-flash-high\n"
        self.assertEqual(
            parse_model_list(raw),
            ["gemini-3.8-flash", "gemini-3.8-pro", "gemini-3.8-flash-high"],
        )

    def test_json_list_of_strings(self):
        self.assertEqual(parse_model_list('["a:1b", "b:2b"]'), ["a:1b", "b:2b"])

    def test_json_object_with_models_key(self):
        # A dict with no recognizable id key, and a non-dict entry, are dropped.
        raw = json.dumps({"models": [{"id": "a"}, {"name": "b"}, {"model": "c"}, {"size": 7}, 7]})
        self.assertEqual(parse_model_list(raw), ["a", "b", "c"])

    def test_json_object_with_data_key(self):
        self.assertEqual(parse_model_list('{"data": [{"id": "x"}]}'), ["x"])

    def test_prose_and_empty_input_yield_nothing(self):
        self.assertEqual(parse_model_list(""), [])
        self.assertEqual(parse_model_list("   \n\n"), [])
        self.assertEqual(parse_model_list("!! not a model id"), [])

    def test_duplicates_removed_and_order_preserved(self):
        self.assertEqual(parse_model_list("b\na\nb\n"), ["b", "a"])

    def test_listing_is_capped(self):
        raw = "\n".join(f"m{i}" for i in range(200))
        self.assertEqual(len(parse_model_list(raw)), 50)


class ListModelsProbeTest(unittest.TestCase):
    """The doctor-facing ``list_models`` seam: time-boxed and fail-soft."""

    def _agy(self):
        return AgyAdapter(_spec("google", name="agy", command="agy", model="gemini-3.8-flash"))

    def test_default_adapter_reports_unknown(self):
        self.assertIsNone(CodexAdapter(_codex_spec()).list_models())

    def test_unavailable_agy_reports_none_without_spawning(self):
        adapter = self._agy()
        with (
            mock.patch.object(AgyAdapter, "available", return_value=False),
            mock.patch("ai_jury.adapters._spawn") as spawned,
        ):
            self.assertIsNone(adapter.list_models())
        spawned.assert_not_called()

    def test_successful_listing_is_parsed(self):
        proc = mock.Mock(returncode=0, stdout="gemini-3.8-flash\ngemini-3.8-pro\n", stderr="")
        with (
            mock.patch.object(AgyAdapter, "available", return_value=True),
            mock.patch("ai_jury.adapters._spawn", return_value=proc) as spawned,
        ):
            models = self._agy().list_models()
        self.assertEqual(models, ["gemini-3.8-flash", "gemini-3.8-pro"])
        self.assertEqual(spawned.call_args.args[0], ["agy", "models"])

    def test_nonzero_exit_reports_none(self):
        proc = mock.Mock(returncode=1, stdout="", stderr="boom")
        with (
            mock.patch.object(AgyAdapter, "available", return_value=True),
            mock.patch("ai_jury.adapters._spawn", return_value=proc),
        ):
            self.assertIsNone(self._agy().list_models())

    def test_spawn_failure_reports_none(self):
        with (
            mock.patch.object(AgyAdapter, "available", return_value=True),
            mock.patch("ai_jury.adapters._spawn", side_effect=OSError("nope")),
        ):
            self.assertIsNone(self._agy().list_models())

    def test_unparseable_listing_reports_none(self):
        proc = mock.Mock(returncode=0, stdout="!! nothing usable", stderr="")
        with (
            mock.patch.object(AgyAdapter, "available", return_value=True),
            mock.patch("ai_jury.adapters._spawn", return_value=proc),
        ):
            self.assertIsNone(self._agy().list_models())

    def test_local_adapter_reports_the_endpoint_listing(self):
        spec = _spec("local", model="qwen:7b", endpoint="http://localhost:11434/v1")
        with mock.patch("ai_jury.adapters.list_local_models", return_value=["qwen:7b"]):
            self.assertEqual(LocalAdapter(spec).list_models(), ["qwen:7b"])
        with mock.patch("ai_jury.adapters.list_local_models", return_value=[]):
            self.assertIsNone(LocalAdapter(spec).list_models())


if __name__ == "__main__":
    unittest.main()
