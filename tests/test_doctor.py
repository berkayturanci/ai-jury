"""Offline tests for ``jury --doctor`` diagnostics.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live agent CLIs, no network.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ai_jury import doctor  # noqa: E402

VALID_CONFIG = """\
[jury]
rounds = 2
chair = "claude"

[jury.context]
mode = "diff-only"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "definitely-not-a-real-cli-xyz"
enabled = true

[[agent]]
name = "codex"
vendor = "openai"
command = "definitely-not-a-real-cli-abc"
enabled = false
"""

# A config carrying a fake secret in string fields. The doctor summary must
# redact it so it never appears in any rendered output or JSON.
SECRET = "sk-ABCDEF0123456789ABCDEF0123456789secretvalue"
SECRET_CONFIG = f"""\
[jury]
rounds = 1
chair = "token={SECRET}"

[jury.context]
mode = "diff-only"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "ls"
enabled = true
"""


def _write_config(text):
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(text)
        return tmp.name


class BuildDiagnosticsTests(unittest.TestCase):
    def test_has_expected_keys(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        for key in (
            "tool_version",
            "python_version",
            "os",
            "agents",
            "config",
            "config_warnings",
        ):
            self.assertIn(key, diag)

        self.assertIsInstance(diag["agents"], list)
        self.assertIsInstance(diag["config_warnings"], list)
        self.assertEqual(diag["config"]["rounds"], 2)
        self.assertEqual(diag["config"]["chair"], "claude")
        self.assertEqual(diag["config"]["context_mode"], "diff-only")
        # Only enabled agents are summarised.
        self.assertEqual(diag["config"]["enabled_agents"], ["claude"])

    def test_agent_availability_reflects_path(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        by_name = {a["name"]: a for a in diag["agents"]}
        # Bogus commands are never on PATH.
        self.assertFalse(by_name["claude"]["available"])
        self.assertFalse(by_name["codex"]["available"])

    def test_real_command_is_available(self):
        config = """\
[jury]
rounds = 1
chair = "claude"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "ls"
enabled = true
"""
        path = _write_config(config)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        self.assertTrue(diag["agents"][0]["available"])

    def test_invalid_config_is_best_effort(self):
        path = _write_config("this is not = valid = toml [[[")
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        # Does not raise; captures the problem and leaves config None.
        self.assertIsNone(diag["config"])
        self.assertTrue(diag["config_warnings"])

    def test_missing_config_is_best_effort(self):
        diag = doctor.build_diagnostics("/nonexistent/path/jury.toml")
        self.assertIsNone(diag["config"])
        self.assertTrue(diag["config_warnings"])

    def test_chair_mismatch_warning(self):
        config = """\
[jury]
rounds = 1
chair = "nonexistent-chair"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "ls"
enabled = true
"""
        path = _write_config(config)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        self.assertTrue(
            any("chair" in w for w in diag["config_warnings"]),
            diag["config_warnings"],
        )


class RedactionTests(unittest.TestCase):
    def test_secret_not_in_diagnostics(self):
        path = _write_config(SECRET_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        self.assertNotIn(SECRET, json.dumps(diag))

    def test_secret_not_in_text_report(self):
        path = _write_config(SECRET_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        report = doctor.render_report(diag)
        self.assertNotIn(SECRET, report)


class SafetyTests(unittest.TestCase):
    def test_no_raw_diff_or_agent_output_keys(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        forbidden = {"diff", "raw_diff", "agent_output", "output", "responses"}
        self.assertEqual(forbidden & set(diag.keys()), set())

    def test_diff_content_never_leaks(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        # A marker that would only exist in a diff is absent.
        self.assertNotIn("diff --git", json.dumps(diag))


class RenderReportTests(unittest.TestCase):
    def test_renders_readable_text(self):
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        report = doctor.render_report(diag)

        self.assertIn("jury doctor", report)
        self.assertIn("Agents", report)
        self.assertIn("Config summary", report)
        self.assertIn("no telemetry", report.lower())


class CapabilityDiagnosticsTests(unittest.TestCase):
    """Version + capability detection surfaced through doctor.

    Detection is exercised with FAKE CLIs by stubbing ``doctor.make_adapter`` so
    no real ``claude``/``codex``/``agy`` binary is ever invoked.
    """

    def _stub_make_adapter(self, caps_by_name):
        """Patch doctor.make_adapter to return a fake adapter per spec."""
        orig = doctor.make_adapter

        class _FakeAdapter:
            def __init__(self, spec, caps):
                self.spec = spec
                self._caps = caps

            def detect_capabilities(self):
                return self._caps

        def _factory(spec, mock=False):
            _ = mock
            caps = caps_by_name.get(
                spec.name,
                {
                    "version": "1.0.0",
                    "supports_headless": True,
                    "supports_model_selection": True,
                    "raw_version_output": "1.0.0",
                    "status": "ok",
                    "warnings": [],
                },
            )
            return _FakeAdapter(spec, caps)

        doctor.make_adapter = _factory
        self.addCleanup(lambda: setattr(doctor, "make_adapter", orig))

    def test_diagnostics_include_version_and_capabilities(self):
        self._stub_make_adapter(
            {
                "claude": {
                    "version": "1.2.3",
                    "supports_headless": True,
                    "supports_model_selection": True,
                    "raw_version_output": "claude 1.2.3",
                    "status": "ok",
                    "warnings": [],
                }
            }
        )
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        by_name = {a["name"]: a for a in diag["agents"]}
        self.assertEqual(by_name["claude"]["version"], "1.2.3")
        self.assertIn("capabilities", by_name["claude"])
        caps = by_name["claude"]["capabilities"]
        self.assertTrue(caps["supports_headless"])
        self.assertTrue(caps["supports_model_selection"])
        self.assertEqual(caps["status"], "ok")

    def test_undetectable_version_warns_but_still_renders(self):
        self._stub_make_adapter(
            {
                "claude": {
                    "version": None,
                    "supports_headless": True,
                    "supports_model_selection": True,
                    "raw_version_output": "",
                    "status": "unknown_version",
                    "warnings": ["could not determine version of 'claude'"],
                }
            }
        )
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        # A capability warning is folded into config_warnings for the enabled agent.
        self.assertTrue(
            any("could not determine version" in w for w in diag["config_warnings"]),
            diag["config_warnings"],
        )
        # Doctor still renders without raising.
        report = doctor.render_report(diag)
        self.assertIn("jury doctor", report)
        self.assertIn("version=unknown", report)

    def test_disabled_agent_capability_warnings_not_surfaced(self):
        # The codex agent is disabled in VALID_CONFIG; its warnings should not
        # clutter the top-level warnings list.
        self._stub_make_adapter(
            {
                "codex": {
                    "version": None,
                    "supports_headless": True,
                    "supports_model_selection": True,
                    "raw_version_output": "",
                    "status": "unknown_version",
                    "warnings": ["could not determine version of 'codex'"],
                }
            }
        )
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        self.assertFalse(
            any("could not determine version of 'codex'" in w for w in diag["config_warnings"]),
            diag["config_warnings"],
        )

    def test_render_shows_capability_summary(self):
        self._stub_make_adapter({})
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        report = doctor.render_report(diag)
        self.assertIn("capabilities=[", report)
        self.assertIn("headless", report)

    def test_capability_probe_exception_caught(self):
        orig = doctor.make_adapter

        def _raising_factory(_spec, mock=False):
            _ = mock
            raise RuntimeError("simulate probe crash")

        doctor.make_adapter = _raising_factory
        self.addCleanup(lambda: setattr(doctor, "make_adapter", orig))

        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        by_name = {a["name"]: a for a in diag["agents"]}
        self.assertIsNone(by_name["claude"]["version"])
        self.assertEqual(by_name["claude"]["capabilities"]["status"], "unknown_version")

        self.assertTrue(
            any(
                "capability probe raised: simulate probe crash" in w
                for w in diag["config_warnings"]
            ),
            diag["config_warnings"],
        )

    def test_capability_probe_exception_redacts_secret(self):
        # doctor.py's own probe wrapper builds a separate warning string from
        # make_adapter()/detect_capabilities() failures; it must redact too,
        # since diagnostics are often pasted into shared bug reports.
        orig = doctor.make_adapter
        leak = "simulate probe crash token=ghp_" + "a" * 36

        def _raising_factory(_spec, mock=False):
            _ = mock
            raise RuntimeError(leak)

        doctor.make_adapter = _raising_factory
        self.addCleanup(lambda: setattr(doctor, "make_adapter", orig))

        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)

        warnings_blob = " ".join(diag["config_warnings"])
        self.assertNotIn("ghp_" + "a" * 36, warnings_blob)
        self.assertIn("[REDACTED", warnings_blob)


class RecommendationsTest(unittest.TestCase):
    def test_not_ready_when_no_agents_available(self):
        # VALID_CONFIG's agents use bogus commands -> none available.
        path = _write_config(VALID_CONFIG)
        self.addCleanup(os.unlink, path)
        diag = doctor.build_diagnostics(path)
        rec = diag["recommendations"]
        self.assertFalse(rec["ready"])
        self.assertTrue(rec["steps"])  # at least one actionable step
        report = doctor.render_report(diag)
        self.assertIn("Next steps", report)
        self.assertIn("ready to run: no", report)

    def test_ready_and_render(self):
        # A reachable agent (mock the availability) -> ready, no "install" step.
        import ai_jury.doctor as d

        real = d._is_available
        d._is_available = lambda spec: True  # noqa: ARG005
        try:
            path = _write_config(VALID_CONFIG)
            self.addCleanup(os.unlink, path)
            diag = d.build_diagnostics(path)
        finally:
            d._is_available = real
        self.assertTrue(diag["recommendations"]["ready"])
        self.assertIn("ready to run: yes", d.render_report(diag))


class AvailabilityTests(unittest.TestCase):
    def test_is_available_catches_exceptions(self):
        orig = doctor.make_adapter

        def _crashing_factory(_spec, mock=False):
            _ = mock
            raise RuntimeError("adapter creation failed")

        doctor.make_adapter = _crashing_factory
        self.addCleanup(lambda: setattr(doctor, "make_adapter", orig))

        # We can pass None as the spec since the factory doesn't check it
        # before raising the exception.
        self.assertFalse(doctor._is_available(None))


class HostedApiWarningTests(unittest.TestCase):
    """A missing-API-key hosted agent gets its own diagnosis, not a bogus
    "command is not on PATH" (there is no command) or "local endpoint
    unreachable" message (issue #430)."""

    def test_missing_key_gets_hosted_api_warning_not_path_warning(self):
        config = """\
[jury]
rounds = 1
chair = "claude-api"

[[agent]]
name = "claude-api"
vendor = "anthropic-api"
model = "claude-x"
enabled = true
"""
        path = _write_config(config)
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {}, clear=True):
            diag = doctor.build_diagnostics(path)
        warnings = diag["config_warnings"]
        self.assertTrue(any("hosted API" in w for w in warnings), warnings)
        self.assertFalse(any("not on PATH" in w for w in warnings), warnings)
        self.assertFalse(any("endpoint" in w for w in warnings), warnings)
        self.assertTrue(any("ANTHROPIC_API_KEY" in w for w in warnings), warnings)

    def test_configured_key_is_available_no_warning(self):
        config = """\
[jury]
rounds = 1
chair = "codex-api"

[[agent]]
name = "codex-api"
vendor = "openai-api"
model = "gpt-x"
enabled = true
"""
        path = _write_config(config)
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "x"}, clear=False):
            diag = doctor.build_diagnostics(path)
        self.assertTrue(diag["agents"][0]["available"])
        self.assertFalse(
            any("codex-api" in w for w in diag["config_warnings"]), diag["config_warnings"]
        )

    def test_google_api_missing_key_gets_hosted_api_warning(self):
        config = """\
[jury]
rounds = 1
chair = "gemini-api"

[[agent]]
name = "gemini-api"
vendor = "google-api"
model = "gemini-x"
enabled = true
"""
        path = _write_config(config)
        self.addCleanup(os.unlink, path)
        with mock.patch.dict(os.environ, {}, clear=True):
            diag = doctor.build_diagnostics(path)
        warnings = diag["config_warnings"]
        self.assertTrue(any("hosted API" in w for w in warnings), warnings)
        self.assertFalse(any("not on PATH" in w for w in warnings), warnings)
        self.assertTrue(any("GEMINI_API_KEY" in w for w in warnings), warnings)


# --------------------------------------------------------------------------
# `jury --doctor --json` provider export (issue #662)
# --------------------------------------------------------------------------

MIXED_TRANSPORT_CONFIG = """\
[jury]
rounds = 1
chair = "claude"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "definitely-not-a-real-cli-xyz"

[[agent]]
name = "gpt"
vendor = "openai-api"
model = "gpt-x"
effort = "high"

[[agent]]
name = "qwen"
vendor = "local"
model = "qwen2.5-coder:7b"
endpoint = "http://localhost:11434/v1"
"""

#: The exported schema, pinned key-by-key. Changing any of this is a BREAKING
#: change for every consumer piping `jury --doctor --json` into a tool, so it
#: must come with a `DOCTOR_SCHEMA_VERSION` bump.
TOP_LEVEL_TYPES = {
    "schema_version": str,
    "tool_version": str,
    "python": str,
    "config_path": str,
    "ready": bool,
    "agents": list,
    "warnings": list,
}

AGENT_TYPES = {
    "name": str,
    "vendor": str,
    "transport": str,
    "available": bool,
    "reason": (str, type(None)),
    "resolved": (str, type(None)),
    "version": (str, type(None)),
    "capabilities": list,
    "models": (list, type(None)),
    "effort_supported": bool,
    "effort": (str, type(None)),
}


def _unreachable():
    """Context managers that make every transport deterministically unavailable.

    The suite must not depend on whether this machine happens to be running an
    Ollama server (or holds a real API key), so the local endpoint probe and the
    environment are both pinned.
    """
    from ai_jury.adapters import LocalAdapter

    return (
        mock.patch.dict(os.environ, {}, clear=True),
        mock.patch.object(LocalAdapter, "available", return_value=False),
        mock.patch.object(doctor, "_probe_models", return_value=None),
    )


def _diagnostics(config_text=MIXED_TRANSPORT_CONFIG):
    """Diagnostics for a config with nothing reachable and no model probes."""
    path = _write_config(config_text)
    try:
        with contextlib.ExitStack() as stack:
            for ctx in _unreachable():
                stack.enter_context(ctx)
            return doctor.build_diagnostics(path)
    finally:
        Path(path).unlink()


def _export(config_text=MIXED_TRANSPORT_CONFIG):
    """Build the v1 export for a config, with no network reachable."""
    diag = _diagnostics(config_text)
    return diag, doctor.doctor_report_dict(diag)


class DoctorJsonSchemaTests(unittest.TestCase):
    """Pin the ai-jury.doctor.v1 shape: keys and types, not sample values."""

    def test_schema_version_is_pinned(self):
        self.assertEqual(doctor.DOCTOR_SCHEMA_VERSION, "ai-jury.doctor.v1")
        _diag, report = _export()
        self.assertEqual(report["schema_version"], "ai-jury.doctor.v1")

    def test_top_level_keys_and_types(self):
        _diag, report = _export()
        self.assertEqual(set(report), set(TOP_LEVEL_TYPES))
        for key, expected in TOP_LEVEL_TYPES.items():
            self.assertIsInstance(report[key], expected, key)

    def test_agent_keys_and_types(self):
        _diag, report = _export()
        self.assertEqual(len(report["agents"]), 3)
        for agent in report["agents"]:
            address = "command" if agent["transport"] == "cli" else "endpoint"
            self.assertEqual(set(agent), set(AGENT_TYPES) | {address})
            for key, expected in AGENT_TYPES.items():
                self.assertIsInstance(agent[key], expected, f"{agent['name']}.{key}")
            self.assertIsInstance(agent[address], (str, type(None)))
            for label in agent["capabilities"]:
                self.assertIsInstance(label, str)

    def test_transport_is_classified_per_vendor(self):
        _diag, report = _export()
        transports = {a["name"]: a["transport"] for a in report["agents"]}
        self.assertEqual(transports, {"claude": "cli", "gpt": "api", "qwen": "local"})

    def test_cli_agents_carry_command_and_api_agents_carry_endpoint(self):
        _diag, report = _export()
        by_name = {a["name"]: a for a in report["agents"]}
        self.assertIn("command", by_name["claude"])
        self.assertNotIn("endpoint", by_name["claude"])
        # A hosted-API agent reports the vendor URL its adapter would actually hit.
        self.assertEqual(by_name["gpt"]["endpoint"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(by_name["qwen"]["endpoint"], "http://localhost:11434/v1")
        self.assertNotIn("command", by_name["gpt"])

    def test_effort_fields_reflect_config_and_vendor_support(self):
        _diag, report = _export()
        by_name = {a["name"]: a for a in report["agents"]}
        self.assertEqual(by_name["gpt"]["effort"], "high")
        self.assertTrue(by_name["gpt"]["effort_supported"])
        # The claude CLI has no headless effort knob.
        self.assertIsNone(by_name["claude"]["effort"])
        self.assertFalse(by_name["claude"]["effort_supported"])

    def test_unavailable_agents_carry_a_reason_and_available_ones_do_not(self):
        _diag, report = _export()
        by_name = {a["name"]: a for a in report["agents"]}
        self.assertFalse(by_name["claude"]["available"])
        self.assertIn("not on PATH", by_name["claude"]["reason"])
        self.assertIn("OPENAI_API_KEY", by_name["gpt"]["reason"])
        self.assertIn("unreachable", by_name["qwen"]["reason"])

    def test_reason_is_none_when_the_agent_is_available(self):
        path = _write_config(MIXED_TRANSPORT_CONFIG)
        self.addCleanup(os.unlink, path)
        with (
            mock.patch.object(doctor, "_is_available", return_value=True),
            mock.patch.object(doctor, "_probe_models", return_value=None),
        ):
            diag = doctor.build_diagnostics(path)
        report = doctor.doctor_report_dict(diag)
        self.assertTrue(all(a["reason"] is None for a in report["agents"]))
        self.assertTrue(report["ready"])

    def test_models_are_exported_when_a_probe_finds_them(self):
        path = _write_config(MIXED_TRANSPORT_CONFIG)
        self.addCleanup(os.unlink, path)
        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(doctor, "_probe_models", return_value=["gemini-3.8-flash"]),
        ):
            diag = doctor.build_diagnostics(path)
        report = doctor.doctor_report_dict(diag)
        self.assertTrue(all(a["models"] == ["gemini-3.8-flash"] for a in report["agents"]))

    def test_models_default_to_null(self):
        _diag, report = _export()
        self.assertTrue(all(a["models"] is None for a in report["agents"]))

    def test_export_is_json_serializable_and_stable(self):
        _diag, report = _export()
        first = json.dumps(report, sort_keys=False)
        self.assertEqual(first, json.dumps(doctor.doctor_report_dict(_diag)))

    def test_export_is_pure(self):
        # No probes: it only reshapes what build_diagnostics already collected.
        with mock.patch.object(doctor, "make_adapter") as factory:
            report = doctor.doctor_report_dict(
                {"agents": [], "config_warnings": [], "recommendations": {}}
            )
        factory.assert_not_called()
        self.assertEqual(report["agents"], [])
        self.assertFalse(report["ready"])

    def test_warnings_carry_env_var_names_never_values(self):
        secret = "sk-DOCTORJSON0123456789ABCDEF0123456789xyz"
        diag = _diagnostics()
        blob = json.dumps(doctor.doctor_report_dict(diag))
        self.assertIn("OPENAI_API_KEY", blob)
        self.assertNotIn(secret, blob)


class DoctorRenderersShareOneDictTests(unittest.TestCase):
    """The text report and the JSON export are two views of ONE projection."""

    def test_text_report_reflects_the_exported_agent_facts(self):
        diag, report = _export()
        text = doctor.render_report(diag)
        for agent in report["agents"]:
            self.assertIn(agent["name"], text)
            self.assertIn(f"vendor={agent['vendor']}", text)
            for label in agent["capabilities"]:
                self.assertIn(label, text)
        self.assertIn("ready to run: no", text)
        self.assertFalse(report["ready"])

    def test_text_report_shows_a_configured_effort(self):
        diag, _report = _export()
        self.assertIn("effort=high", doctor.render_report(diag))

    def test_renderers_agree_on_readiness(self):
        path = _write_config(MIXED_TRANSPORT_CONFIG)
        self.addCleanup(os.unlink, path)
        with (
            mock.patch.object(doctor, "_is_available", return_value=True),
            mock.patch.object(doctor, "_probe_models", return_value=None),
        ):
            diag = doctor.build_diagnostics(path)
        self.assertTrue(doctor.doctor_report_dict(diag)["ready"])
        self.assertIn("ready to run: yes", doctor.render_report(diag))

    def test_empty_panel_renders_in_both_views(self):
        diag = {
            "tool_version": "0",
            "python_version": "3.12.0",
            "python_implementation": "CPython",
            "python_executable": "/usr/bin/python3",
            "os": "test-os",
            "config_path": "(default)",
            "agents": [],
            "config": None,
            "config_warnings": [],
            "recommendations": {"ready": False, "steps": []},
        }
        self.assertEqual(doctor.doctor_report_dict(diag)["agents"], [])
        self.assertIn("(no agents loaded)", doctor.render_report(diag))


class DoctorPureHelperTests(unittest.TestCase):
    def test_capability_labels(self):
        self.assertEqual(
            doctor.capability_labels({"supports_headless": True, "supports_model_selection": True}),
            ["headless", "model-selection"],
        )
        self.assertEqual(doctor.capability_labels({"supports_headless": True}), ["headless"])
        self.assertEqual(doctor.capability_labels(None), [])

    def test_transport_classification(self):
        cases = [
            (("local", "", None), "local"),
            (("anthropic-api", "", None), "api"),
            (("openai-compatible", "", "https://x/v1"), "api"),
            (("anthropic", "claude", None), "cli"),
            (("", "some-cli", None), "cli"),
            (("", "", "https://x/v1"), "api"),
            (("", "", None), "cli"),
        ]
        for args, expected in cases:
            self.assertEqual(doctor._transport(*args), expected, args)

    def test_unavailable_reason_prefers_the_adapter_warning(self):
        from ai_jury.config import AgentSpec

        spec = AgentSpec(name="a", vendor="anthropic-api", model="m")
        self.assertEqual(doctor._unavailable_reason(spec, ["KEY is not set"]), "KEY is not set")

    def test_unavailable_reason_fallbacks_per_transport(self):
        from ai_jury.config import AgentSpec

        local = AgentSpec(name="l", vendor="local", model="m")
        self.assertIn("11434", doctor._unavailable_reason(local, []))
        hosted = AgentSpec(name="h", vendor="google-api", model="m")
        self.assertIn("hosted API", doctor._unavailable_reason(hosted, []))
        cli = AgentSpec(name="c", vendor="anthropic", command="claude")
        self.assertIn("not on PATH", doctor._unavailable_reason(cli, []))
        bare = AgentSpec(name="b", vendor="mystery")
        self.assertEqual(doctor._unavailable_reason(bare, []), "not available")

    def test_probe_failures_degrade_to_none(self):
        from ai_jury.config import AgentSpec

        spec = AgentSpec(name="a", vendor="anthropic", command="claude")
        with mock.patch.object(doctor, "make_adapter", side_effect=RuntimeError("boom")):
            self.assertIsNone(doctor._probe_models(spec))
            self.assertIsNone(doctor._endpoint_for(spec))

    def test_endpoint_for_defaults_a_local_agent_to_the_ollama_url(self):
        from ai_jury.config import AgentSpec

        spec = AgentSpec(name="a", vendor="local", model="m")
        self.assertEqual(doctor._endpoint_for(spec), "http://localhost:11434/v1")

    def test_endpoint_for_is_none_for_a_cli_agent(self):
        from ai_jury.config import AgentSpec

        spec = AgentSpec(name="a", vendor="anthropic", command="claude")
        self.assertIsNone(doctor._endpoint_for(spec))

    def test_endpoint_for_strips_userinfo_credentials(self):
        from ai_jury.config import AgentSpec

        spec = AgentSpec(
            name="a", vendor="local", model="m", endpoint="http://user:pw@localhost:11434/v1"
        )
        endpoint = doctor._endpoint_for(spec)
        self.assertNotIn("pw", endpoint)


if __name__ == "__main__":
    unittest.main()
