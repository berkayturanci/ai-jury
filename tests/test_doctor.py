"""Offline tests for ``council --doctor`` diagnostics.

Run with: python -m unittest discover -s tests
No third-party dependencies, no live agent CLIs, no network.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_review_council import doctor  # noqa: E402


VALID_CONFIG = """\
[council]
rounds = 2
chair = "claude"

[council.context]
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
SECRET_CONFIG = """\
[council]
rounds = 1
chair = "token={secret}"

[council.context]
mode = "diff-only"

[[agent]]
name = "claude"
vendor = "anthropic"
command = "ls"
enabled = true
""".format(secret=SECRET)


def _write_config(text):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".toml", delete=False, encoding="utf-8"
    )
    tmp.write(text)
    tmp.close()
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
[council]
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
        diag = doctor.build_diagnostics("/nonexistent/path/council.toml")
        self.assertIsNone(diag["config"])
        self.assertTrue(diag["config_warnings"])

    def test_chair_mismatch_warning(self):
        config = """\
[council]
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

        self.assertIn("council doctor", report)
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
        self.assertIn("council doctor", report)
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
        import agent_review_council.doctor as d

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


if __name__ == "__main__":
    unittest.main()
